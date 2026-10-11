import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { DocumentResult, MaintainedResult, RecipeContent } from "@/lib/api";

type Content = NonNullable<MaintainedResult["content"]>;

function safeExternalUrl(href: string | undefined) {
  if (!href || href.trim() !== href || /[\u0000-\u001f]/.test(href)) return false;
  try {
    const url = new URL(href);
    return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname) && !url.username && !url.password;
  } catch { return false; }
}

function DocumentView({ content, publicId }: { content: Content; publicId?: string }) {
  const document = content as NonNullable<DocumentResult["content"]>;
  return <div className="prose prose-invert max-w-none break-words leading-relaxed text-zinc-200">
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
      img: () => null,
      a: ({ href, children }) => !safeExternalUrl(href) ? <span>{children}</span> :
        <a href={href} target="_blank" rel="noopener noreferrer" className="text-sky-400 underline">{children}</a>,
    }}>{document.body}</ReactMarkdown>
    {document.sources?.length ? <section className="mt-6"><h2>Sources</h2><ul>{document.sources.map((source, index) => <li key={index}>
      {safeExternalUrl(source.url) ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a> : source.label}
    </li>)}</ul></section> : null}
    {publicId && document.attachments?.length ? <section className="mt-6"><h2>Attachments</h2><ul>{document.attachments.map((attachment, index) => <li key={index}>
      <a href={`/api/results/${encodeURIComponent(publicId)}/attachments/${encodeURIComponent(attachment.artifact_id)}`} className="text-sky-400 underline">{attachment.label}</a>
    </li>)}</ul></section> : null}
  </div>;
}

function RecipeView({ content }: { content: Content }) {
  const recipe = content as RecipeContent;
  return <div className="space-y-6 leading-relaxed">
    {recipe.uncertainty && <p className="text-amber-300">{recipe.uncertainty}</p>}
    {(recipe.servings || recipe.timing) && <p className="text-zinc-400">{[recipe.servings, recipe.timing].filter(Boolean).join(" · ")}</p>}
    <section><h2 className="mb-3 text-xl font-semibold">Ingredients</h2>
      <ul className="list-disc space-y-2 pl-6">{recipe.ingredients.map((ingredient, index) => <li key={index}>{ingredient}</li>)}</ul>
    </section>
    <section><h2 className="mb-3 text-xl font-semibold">Instructions</h2>
      <ol className="list-decimal space-y-3 pl-6">{recipe.instructions.map((step, index) => <li key={index}>{step}</li>)}</ol>
    </section>
    <section><h2 className="mb-3 text-xl font-semibold">Sources</h2>
      <ul className="space-y-2">{recipe.sources.map((source, index) => <li key={index}>
        {safeExternalUrl(source.url) ? <a href={source.url} target="_blank" rel="noopener noreferrer" className="text-sky-400 underline">{source.title}</a> : source.title}
      </li>)}</ul>
    </section>
  </div>;
}

export const resultRenderers: Record<string, { label: string; View: React.ComponentType<{ content: Content; publicId?: string }> }> = {
  "document.v1": { label: "Documents", View: DocumentView },
  "recipe.v1": { label: "Recipes", View: RecipeView },
};
