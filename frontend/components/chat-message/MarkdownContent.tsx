"use client";

import clsx from "clsx";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  h1: ({ children }) => <h1 className="mb-2 mt-4 text-xl font-semibold text-zinc-100 first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-4 text-lg font-semibold text-zinc-100 first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-3 font-semibold text-zinc-100 first:mt-0">{children}</h3>,
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-zinc-100">{children}</strong>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-zinc-500">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-zinc-500">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  a: ({ children, href }) => (
    <a className="text-sky-400 underline decoration-sky-500/40 underline-offset-2 hover:text-sky-300" href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-zinc-700 pl-3 text-zinc-400">{children}</blockquote>,
  code: ({ children, className }) => (
    <code className={clsx("rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[0.9em] text-zinc-200", className)}>{children}</code>
  ),
  pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950 p-3 text-xs leading-6 [&>code]:bg-transparent [&>code]:p-0">{children}</pre>,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-zinc-900 text-zinc-200">{children}</thead>,
  th: ({ children }) => <th className="border-b border-zinc-800 px-3 py-2 font-medium">{children}</th>,
  td: ({ children }) => <td className="border-b border-zinc-800/70 px-3 py-2 align-top last:border-b-0">{children}</td>,
  hr: () => <hr className="my-4 border-zinc-800" />,
};

export function MarkdownContent({ content, className }: { content: string; className?: string }) {
  return (
    <div className={clsx("min-w-0 break-words", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{content}</ReactMarkdown>
    </div>
  );
}
