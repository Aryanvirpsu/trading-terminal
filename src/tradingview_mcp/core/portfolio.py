import sqlite3
import os
from typing import Dict, List, Optional, Any
from datetime import datetime

# Store the DB in the user's home directory or current directory
DB_DIR = os.path.expanduser("~/.tradingview_mcp_data")
DB_PATH = os.path.join(DB_DIR, "portfolio.db")

def init_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Table for users and their paper trading balance
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        balance REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Table for active positions
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        quantity REAL NOT NULL,
        average_price REAL NOT NULL,
        side TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # Table for trade history/logs
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        quantity REAL NOT NULL,
        price REAL NOT NULL,
        side TEXT NOT NULL,  -- 'BUY' or 'SELL'
        realized_pnl REAL DEFAULT 0,
        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Table for open option positions (long calls/puts only — no naked
    # writing/margin modeling; mirrors the simple long-only stock model).
    # One row per distinct (user, underlying, type, strike, expiry) contract.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS option_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        underlying_symbol TEXT NOT NULL,
        option_type TEXT NOT NULL,     -- 'CALL' or 'PUT'
        strike REAL NOT NULL,
        expiry TEXT NOT NULL,          -- ISO date YYYY-MM-DD
        quantity REAL NOT NULL,        -- number of contracts
        average_premium REAL NOT NULL, -- per-contract premium (per share, pre-multiplier)
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')

    # Table for option trade history/logs
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS option_trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        underlying_symbol TEXT NOT NULL,
        option_type TEXT NOT NULL,
        strike REAL NOT NULL,
        expiry TEXT NOT NULL,
        quantity REAL NOT NULL,
        premium REAL NOT NULL,
        side TEXT NOT NULL,  -- 'BUY' (open/add) or 'SELL' (close)
        realized_pnl REAL DEFAULT 0,
        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Trade journal — the strategy doc requires every trade to have a documented
    # thesis, entry, stop, target(s), and R:R BEFORE capital is committed, then
    # be reviewed after close. This table is that journal; it is intentionally
    # separate from the fill logs (trade_history / option_trade_history), which
    # only record executions. One row per planned/committed trade idea.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trade_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        instrument_type TEXT NOT NULL,   -- 'STOCK' or 'OPTION'
        setup_type TEXT,                 -- momentum / breakout / catalyst / high_rvol
        thesis TEXT,
        entry REAL,
        stop REAL,
        targets TEXT,                    -- JSON list of target prices
        risk_reward REAL,
        planned_risk_usd REAL,           -- $ at risk per the plan
        quantity REAL,
        status TEXT DEFAULT 'open',      -- 'open' / 'closed' / 'cancelled'
        outcome_pnl REAL DEFAULT 0,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP
    )
    ''')

    # Option journal entries need to fully identify the contract so exit
    # management can re-price them from the live chain. Added via ALTER (not in
    # the CREATE above) so databases created by an earlier version upgrade
    # cleanly instead of erroring on the existing table.
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(trade_journal)")}
    for col, decl in (("option_type", "TEXT"), ("option_strike", "REAL"), ("option_expiry", "TEXT")):
        if col not in existing_cols:
            cursor.execute(f"ALTER TABLE trade_journal ADD COLUMN {col} {decl}")
            
    # Add new columns for AI Trading Intelligence Platform to trade_history
    existing_cols_th = {row[1] for row in cursor.execute("PRAGMA table_info(trade_history)")}
    new_cols_th = [
        ("market_condition", "TEXT"), ("sector_condition", "TEXT"),
        ("news_snapshot", "TEXT"), ("technical_snapshot", "TEXT"),
        ("mfe", "REAL"), ("mae", "REAL"), ("missed_profit", "REAL"),
        ("ai_failure_impact", "TEXT"), ("original_thesis", "TEXT")
    ]
    for col, decl in new_cols_th:
        if col not in existing_cols_th:
            cursor.execute(f"ALTER TABLE trade_history ADD COLUMN {col} {decl}")

    # Add new columns for AI Trading Intelligence Platform to option_trade_history
    existing_cols_oth = {row[1] for row in cursor.execute("PRAGMA table_info(option_trade_history)")}
    for col, decl in new_cols_th:
        if col not in existing_cols_oth:
            cursor.execute(f"ALTER TABLE option_trade_history ADD COLUMN {col} {decl}")

    # Equity snapshots — a periodic log of total account equity so we can plot an
    # equity curve and evaluate the strategy over TIME (the doc's "performance
    # metrics evaluated regularly"), not just as a single scan. Appended by
    # daily_run / manage, never on every dashboard poll.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS equity_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        equity REAL NOT NULL,
        snapped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Missed / passed trades — the "shadow journal." Every setup we DECLINED
    # (gate fail, bad tape, concentration, etc.) recorded with the price at the
    # moment of the decision, so we can later compute the "what-if" P&L and learn
    # whether our discipline actually saved or cost us money over many decisions.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS missed_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        instrument_type TEXT NOT NULL,   -- 'STOCK' or 'OPTION'
        option_type TEXT,
        option_strike REAL,
        option_expiry TEXT,
        reference_price REAL NOT NULL,   -- price (stock) or premium (option) at decision time
        reason TEXT,                     -- why we passed
        decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Post-trade lessons to persist learnings
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS post_trade_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        trade_type TEXT,
        lesson_type TEXT,
        lesson_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # AI Tasks Queue for failover and state persistence
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ai_tasks_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL UNIQUE,
        user_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        current_step TEXT,
        payload TEXT,
        model_used TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()

def get_or_create_user(user_id: str, initial_balance: float = 10000.0) -> float:
    """Returns the current balance of the user. Creates the user with 10k if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        cursor.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, initial_balance))
        conn.commit()
        balance = initial_balance
    else:
        balance = row[0]
        
    conn.close()
    return balance

def execute_trade(user_id: str, symbol: str, quantity: float, current_price: float, side: str) -> Dict[str, Any]:
    """Execute a simulated trade (BUY or SELL) for a user."""
    symbol = symbol.upper()
    side = side.upper()
    
    if side not in ['BUY', 'SELL']:
        return {"error": "Side must be 'BUY' or 'SELL'"}
        
    if quantity <= 0:
        return {"error": "Quantity must be greater than 0"}

    # Initialize user if they don't exist
    balance = get_or_create_user(user_id)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        if side == 'BUY':
            cost = quantity * current_price
            if balance < cost:
                return {"error": f"Insufficient balance. Required: ${cost:.2f}, Available: ${balance:.2f}"}
            
            # Deduct balance
            new_balance = balance - cost
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            
            # Check if position exists to average down, else create new
            cursor.execute("SELECT id, quantity, average_price FROM positions WHERE user_id = ? AND symbol = ?", (user_id, symbol))
            pos = cursor.fetchone()
            
            if pos:
                pos_id, existing_qty, existing_avg_price = pos
                new_qty = existing_qty + quantity
                new_avg_price = ((existing_qty * existing_avg_price) + (quantity * current_price)) / new_qty
                cursor.execute("UPDATE positions SET quantity = ?, average_price = ? WHERE id = ?", (new_qty, new_avg_price, pos_id))
            else:
                cursor.execute("INSERT INTO positions (user_id, symbol, quantity, average_price, side) VALUES (?, ?, ?, ?, ?)", 
                               (user_id, symbol, quantity, current_price, "LONG"))
                
            # Log history
            cursor.execute("INSERT INTO trade_history (user_id, symbol, quantity, price, side) VALUES (?, ?, ?, ?, ?)",
                           (user_id, symbol, quantity, current_price, 'BUY'))
            
            conn.commit()
            return {
                "status": "success", 
                "action": "BUY", 
                "symbol": symbol,
                "quantity": quantity,
                "price": current_price,
                "total_cost": cost,
                "remaining_balance": new_balance
            }
            
        elif side == 'SELL':
            # Check position
            cursor.execute("SELECT id, quantity, average_price FROM positions WHERE user_id = ? AND symbol = ?", (user_id, symbol))
            pos = cursor.fetchone()
            
            if not pos:
                return {"error": f"You do not own any {symbol}"}
                
            pos_id, existing_qty, existing_avg_price = pos
            
            if quantity > existing_qty:
                return {"error": f"Cannot sell {quantity} of {symbol}. You only own {existing_qty}."}
                
            revenue = quantity * current_price
            realized_pnl = (current_price - existing_avg_price) * quantity
            
            # Add to balance
            new_balance = balance + revenue
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            
            # Update or remove position
            new_qty = existing_qty - quantity
            if new_qty <= 0.00001:  # Floating point safety
                cursor.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
            else:
                cursor.execute("UPDATE positions SET quantity = ? WHERE id = ?", (new_qty, pos_id))
                
            # Log history
            cursor.execute("INSERT INTO trade_history (user_id, symbol, quantity, price, side, realized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, symbol, quantity, current_price, 'SELL', realized_pnl))
            
            conn.commit()
            return {
                "status": "success", 
                "action": "SELL", 
                "symbol": symbol,
                "quantity": quantity,
                "price": current_price,
                "revenue": revenue,
                "realized_pnl": realized_pnl,
                "new_balance": new_balance
            }

    except Exception as e:
        conn.rollback()
        return {"error": f"Database error during trade: {str(e)}"}
    finally:
        conn.close()

def get_portfolio(user_id: str) -> Dict[str, Any]:
    """Retrieve the user's current portfolio (balance and open positions)."""
    balance = get_or_create_user(user_id)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT symbol, quantity, average_price FROM positions WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    
    positions = []
    for row in rows:
        positions.append({
            "symbol": row["symbol"],
            "quantity": row["quantity"],
            "average_price": row["average_price"]
        })
        
    conn.close()
    
    return {
        "user_id": user_id,
        "balance": balance,
        "positions": positions
    }

def get_trade_history(user_id: str, limit: int = 50) -> Dict[str, Any]:
    """Retrieve the user's most recent simulated trades, newest first."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT symbol, quantity, price, side, realized_pnl, executed_at "
        "FROM trade_history WHERE user_id = ? ORDER BY executed_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    rows = cursor.fetchall()
    conn.close()

    trades = [
        {
            "symbol": row["symbol"],
            "quantity": row["quantity"],
            "price": row["price"],
            "side": row["side"],
            "realized_pnl": row["realized_pnl"],
            "executed_at": row["executed_at"],
        }
        for row in rows
    ]

    return {"user_id": user_id, "count": len(trades), "trades": trades}

# Standard US equity option contract size (1 contract = 100 shares).
OPTION_CONTRACT_MULTIPLIER = 100

def execute_option_trade(
    user_id: str,
    underlying_symbol: str,
    option_type: str,
    strike: float,
    expiry: str,
    quantity: float,
    current_premium: float,
    side: str,
) -> Dict[str, Any]:
    """Execute a simulated option trade (BUY = open/add long, SELL = close long).

    No naked/short writing — SELL always requires an existing long position,
    same simple model as execute_trade for stocks. All money amounts use the
    100x contract multiplier (1 contract = 100 shares of the underlying).
    """
    underlying_symbol = underlying_symbol.upper()
    option_type = option_type.upper()
    side = side.upper()

    if side not in ['BUY', 'SELL']:
        return {"error": "Side must be 'BUY' or 'SELL'"}
    if option_type not in ['CALL', 'PUT']:
        return {"error": "option_type must be 'CALL' or 'PUT'"}
    if quantity <= 0:
        return {"error": "Quantity must be greater than 0"}
    if current_premium < 0:
        return {"error": "Premium cannot be negative"}

    balance = get_or_create_user(user_id)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id, quantity, average_premium FROM option_positions "
            "WHERE user_id = ? AND underlying_symbol = ? AND option_type = ? AND strike = ? AND expiry = ?",
            (user_id, underlying_symbol, option_type, strike, expiry),
        )
        pos = cursor.fetchone()

        if side == 'BUY':
            cost = quantity * current_premium * OPTION_CONTRACT_MULTIPLIER
            if balance < cost:
                return {"error": f"Insufficient balance. Required: ${cost:.2f}, Available: ${balance:.2f}"}

            new_balance = balance - cost
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))

            if pos:
                pos_id, existing_qty, existing_avg_premium = pos
                new_qty = existing_qty + quantity
                new_avg_premium = ((existing_qty * existing_avg_premium) + (quantity * current_premium)) / new_qty
                cursor.execute("UPDATE option_positions SET quantity = ?, average_premium = ? WHERE id = ?",
                               (new_qty, new_avg_premium, pos_id))
            else:
                cursor.execute(
                    "INSERT INTO option_positions (user_id, underlying_symbol, option_type, strike, expiry, quantity, average_premium) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, underlying_symbol, option_type, strike, expiry, quantity, current_premium),
                )

            cursor.execute(
                "INSERT INTO option_trade_history (user_id, underlying_symbol, option_type, strike, expiry, quantity, premium, side) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, underlying_symbol, option_type, strike, expiry, quantity, current_premium, 'BUY'),
            )

            conn.commit()
            return {
                "status": "success",
                "action": "BUY",
                "underlying_symbol": underlying_symbol,
                "option_type": option_type,
                "strike": strike,
                "expiry": expiry,
                "quantity": quantity,
                "premium": current_premium,
                "total_cost": cost,
                "remaining_balance": new_balance,
            }

        elif side == 'SELL':
            if not pos:
                return {"error": f"You do not own any {underlying_symbol} {expiry} {strike} {option_type}"}

            pos_id, existing_qty, existing_avg_premium = pos
            if quantity > existing_qty:
                return {"error": f"Cannot sell {quantity} contracts. You only own {existing_qty}."}

            revenue = quantity * current_premium * OPTION_CONTRACT_MULTIPLIER
            realized_pnl = (current_premium - existing_avg_premium) * quantity * OPTION_CONTRACT_MULTIPLIER

            new_balance = balance + revenue
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))

            new_qty = existing_qty - quantity
            if new_qty <= 0.00001:
                cursor.execute("DELETE FROM option_positions WHERE id = ?", (pos_id,))
            else:
                cursor.execute("UPDATE option_positions SET quantity = ? WHERE id = ?", (new_qty, pos_id))

            cursor.execute(
                "INSERT INTO option_trade_history (user_id, underlying_symbol, option_type, strike, expiry, quantity, premium, side, realized_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, underlying_symbol, option_type, strike, expiry, quantity, current_premium, 'SELL', realized_pnl),
            )

            conn.commit()
            return {
                "status": "success",
                "action": "SELL",
                "underlying_symbol": underlying_symbol,
                "option_type": option_type,
                "strike": strike,
                "expiry": expiry,
                "quantity": quantity,
                "premium": current_premium,
                "revenue": revenue,
                "realized_pnl": realized_pnl,
                "new_balance": new_balance,
            }

    except Exception as e:
        conn.rollback()
        return {"error": f"Database error during option trade: {str(e)}"}
    finally:
        conn.close()

def get_option_portfolio(user_id: str) -> Dict[str, Any]:
    """Retrieve the user's current balance and open option positions."""
    balance = get_or_create_user(user_id)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT underlying_symbol, option_type, strike, expiry, quantity, average_premium "
        "FROM option_positions WHERE user_id = ?",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    positions = [
        {
            "underlying_symbol": row["underlying_symbol"],
            "option_type": row["option_type"],
            "strike": row["strike"],
            "expiry": row["expiry"],
            "quantity": row["quantity"],
            "average_premium": row["average_premium"],
        }
        for row in rows
    ]

    return {"user_id": user_id, "balance": balance, "positions": positions}

def get_option_trade_history(user_id: str, limit: int = 50) -> Dict[str, Any]:
    """Retrieve the user's most recent simulated option trades, newest first."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT underlying_symbol, option_type, strike, expiry, quantity, premium, side, realized_pnl, executed_at "
        "FROM option_trade_history WHERE user_id = ? ORDER BY executed_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    rows = cursor.fetchall()
    conn.close()

    trades = [
        {
            "underlying_symbol": row["underlying_symbol"],
            "option_type": row["option_type"],
            "strike": row["strike"],
            "expiry": row["expiry"],
            "quantity": row["quantity"],
            "premium": row["premium"],
            "side": row["side"],
            "realized_pnl": row["realized_pnl"],
            "executed_at": row["executed_at"],
        }
        for row in rows
    ]

    return {"user_id": user_id, "count": len(trades), "trades": trades}

def setup_account(user_id: str, initial_balance: float = 500.0, reset: bool = False) -> Dict[str, Any]:
    """Create an account at a specific starting balance (e.g. the $500 strategy
    account). If it already exists: no-op unless reset=True, which wipes all its
    positions, options, fills, and journal entries and restores the balance."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()

        if row is not None and not reset:
            conn.close()
            return {"status": "exists", "user_id": user_id, "balance": row[0],
                    "note": "Account already exists. Pass reset=True to wipe and restart it."}

        if reset:
            for table in ("positions", "trade_history", "option_positions",
                          "option_trade_history", "trade_journal", "equity_snapshots"):
                cursor.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

        if row is None:
            cursor.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, initial_balance))
        else:
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (initial_balance, user_id))

        conn.commit()
        return {"status": "reset" if reset else "created", "user_id": user_id,
                "balance": initial_balance}
    except Exception as e:
        conn.rollback()
        return {"error": f"Database error during account setup: {str(e)}"}
    finally:
        conn.close()


def add_journal_entry(
    user_id: str,
    symbol: str,
    instrument_type: str,
    setup_type: str,
    thesis: str,
    entry: float,
    stop: float,
    targets: List[float],
    risk_reward: float,
    planned_risk_usd: float,
    quantity: float,
    notes: str = "",
    option_type: Optional[str] = None,
    option_strike: Optional[float] = None,
    option_expiry: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a planned/committed trade in the journal (status='open').

    For OPTION entries, option_type/strike/expiry should be supplied so exit
    management can later re-price the exact contract from the live chain.
    """
    import json as _json
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO trade_journal (user_id, symbol, instrument_type, setup_type, thesis, "
            "entry, stop, targets, risk_reward, planned_risk_usd, quantity, status, "
            "option_type, option_strike, option_expiry) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (user_id, symbol.upper(), instrument_type.upper(), setup_type, thesis,
             entry, stop, _json.dumps(targets), risk_reward, planned_risk_usd, quantity,
             option_type.upper() if option_type else None, option_strike, option_expiry),
        )
        conn.commit()
        return {"status": "logged", "journal_id": cursor.lastrowid, "symbol": symbol.upper()}
    except Exception as e:
        conn.rollback()
        return {"error": f"Database error logging journal entry: {str(e)}"}
    finally:
        conn.close()


def record_equity_snapshot(user_id: str, equity: float) -> Dict[str, Any]:
    """Append a timestamped total-equity reading for the equity curve."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO equity_snapshots (user_id, equity) VALUES (?, ?)",
            (user_id, round(equity, 2)),
        )
        conn.commit()
        return {"status": "recorded", "equity": round(equity, 2)}
    except Exception as e:
        conn.rollback()
        return {"error": f"Database error recording equity snapshot: {str(e)}"}
    finally:
        conn.close()


def get_equity_curve(user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Return equity snapshots oldest-first (ready to plot)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT equity, snapped_at FROM equity_snapshots WHERE user_id = ? "
        "ORDER BY snapped_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 1000))),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"equity": r["equity"], "at": r["snapped_at"]} for r in reversed(rows)]


def close_journal_entry(user_id: str, journal_id: int, outcome_pnl: float, notes: str = "") -> Dict[str, Any]:
    """Mark a journal entry closed with its realized outcome and review notes."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE trade_journal SET status='closed', outcome_pnl=?, notes=?, "
            "closed_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
            (outcome_pnl, notes, journal_id, user_id),
        )
        if cursor.rowcount == 0:
            return {"error": f"No open journal entry #{journal_id} for {user_id}"}
        conn.commit()
        return {"status": "closed", "journal_id": journal_id, "outcome_pnl": outcome_pnl}
    except Exception as e:
        conn.rollback()
        return {"error": f"Database error closing journal entry: {str(e)}"}
    finally:
        conn.close()


def get_journal(user_id: str, limit: int = 100) -> Dict[str, Any]:
    """Return the trade journal (planned/committed trades), newest first."""
    import json as _json
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, instrument_type, setup_type, thesis, entry, stop, targets, "
        "risk_reward, planned_risk_usd, quantity, status, outcome_pnl, notes, created_at, closed_at, "
        "option_type, option_strike, option_expiry "
        "FROM trade_journal WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    rows = cursor.fetchall()
    conn.close()

    entries = []
    for r in rows:
        try:
            targets = _json.loads(r["targets"]) if r["targets"] else []
        except (ValueError, TypeError):
            targets = []
        entries.append({
            "id": r["id"], "symbol": r["symbol"], "instrument_type": r["instrument_type"],
            "setup_type": r["setup_type"], "thesis": r["thesis"], "entry": r["entry"],
            "stop": r["stop"], "targets": targets, "risk_reward": r["risk_reward"],
            "planned_risk_usd": r["planned_risk_usd"], "quantity": r["quantity"],
            "status": r["status"], "outcome_pnl": r["outcome_pnl"], "notes": r["notes"],
            "created_at": r["created_at"], "closed_at": r["closed_at"],
            "option_type": r["option_type"], "option_strike": r["option_strike"],
            "option_expiry": r["option_expiry"],
        })

    open_count = sum(1 for e in entries if e["status"] == "open")
    closed = [e for e in entries if e["status"] == "closed"]
    wins = sum(1 for e in closed if (e["outcome_pnl"] or 0) > 0)
    return {
        "user_id": user_id,
        "count": len(entries),
        "open_count": open_count,
        "closed_count": len(closed),
        "win_rate_pct": round(wins / len(closed) * 100, 1) if closed else None,
        "total_realized_pnl": round(sum(e["outcome_pnl"] or 0 for e in closed), 2),
        "entries": entries,
    }


def log_missed_trade(
    user_id: str,
    symbol: str,
    instrument_type: str,
    reference_price: float,
    reason: str = "",
    option_type: Optional[str] = None,
    option_strike: Optional[float] = None,
    option_expiry: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a setup we passed on, with the price/premium at decision time."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO missed_trades (user_id, symbol, instrument_type, option_type, "
            "option_strike, option_expiry, reference_price, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, symbol.upper(), instrument_type.upper(),
             option_type.upper() if option_type else None, option_strike, option_expiry,
             reference_price, reason),
        )
        conn.commit()
        return {"status": "logged", "missed_id": cursor.lastrowid, "symbol": symbol.upper()}
    except Exception as e:
        conn.rollback()
        return {"error": f"Database error logging missed trade: {str(e)}"}
    finally:
        conn.close()

def get_missed_trades_raw(user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Return raw missed-trade records (no live pricing — that's done upstream)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, symbol, instrument_type, option_type, option_strike, option_expiry, "
        "reference_price, reason, decided_at FROM missed_trades WHERE user_id = ? "
        "ORDER BY decided_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 500))),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Initialize DB when module is imported
init_db()
