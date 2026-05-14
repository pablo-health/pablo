// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Markdown renderer.
 *
 * Wraps react-markdown + remark-gfm with the project's Tailwind
 * styling so callers don't have to think about styling each heading,
 * table, list, or blockquote. Pure render — no scripts execute,
 * no raw HTML passthrough.
 *
 * Use for trusted, repo-owned content (security guide, BAA, etc.).
 * Not safe for user-supplied markdown unless the source is verified
 * trustworthy — react-markdown drops HTML by default, but custom
 * remark plugins can change that. Keep this component plugin-light.
 */

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

interface MarkdownProps {
  children: string
  className?: string
}

export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-2xl md:text-3xl font-display font-semibold text-neutral-900 mt-6 mb-3 first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-xl font-display font-semibold text-neutral-900 mt-6 mb-2">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-base font-semibold text-neutral-900 mt-4 mb-2">
              {children}
            </h3>
          ),
          p: ({ children }) => (
            <p className="text-neutral-700 leading-relaxed my-3">{children}</p>
          ),
          ul: ({ children }) => (
            <ul className="list-disc pl-6 my-3 space-y-1 text-neutral-700">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-6 my-3 space-y-1 text-neutral-700">{children}</ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          a: ({ href, children }) => (
            <a
              href={href}
              className="text-primary-600 hover:text-primary-700 underline"
              target={href?.startsWith("http") ? "_blank" : undefined}
              rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-primary-300 bg-primary-50 pl-4 pr-3 py-2 my-4 text-neutral-700 italic">
              {children}
            </blockquote>
          ),
          code: ({ children }) => (
            <code className="bg-neutral-100 text-neutral-900 px-1 py-0.5 rounded text-sm">
              {children}
            </code>
          ),
          hr: () => <hr className="my-6 border-neutral-200" />,
          table: ({ children }) => (
            <div className="overflow-x-auto my-4">
              <table className="min-w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-neutral-50">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border border-neutral-200 px-3 py-2 text-left font-semibold text-neutral-900">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-neutral-200 px-3 py-2 text-neutral-700 align-top">
              {children}
            </td>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-neutral-900">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
