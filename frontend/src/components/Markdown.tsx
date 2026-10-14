import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Renders experiment write-ups. Kept in its own module so the markdown stack loads only with the Testing pages. */
export default function Markdown({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}
