import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ChatResponse } from "./types";
import { EvidencePanel } from "./components/EvidenceTable";

const SUGGESTED_QUESTIONS = [
  "How much did we spend on vendor payouts last month?",
  "Which transactions are still unreconciled?",
  "Which vendors received the most money last month?",
  "How much did we pay ABC Suppliers last month?",
];

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;
    setError(null);
    setInput("");
    setMessages((m) => [
      ...m,
      { role: "user", text: trimmed },
      { role: "assistant", text: "", meta: undefined }, // placeholder while loading
    ]);
    setLoading(true);
    try {
      const resp = await sendRequestWithId(trimmed);
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = {
          role: "assistant",
          text: resp.answer,
          evidence: resp.evidence,
          query: resp.query,
          refusal: resp.refusal,
          meta: resp.meta,
          suggestions: resp.refusal?.suggestions,
        };
        return copy;
      });
      if (resp.conversation_id && resp.conversation_id !== "anonymous") {
        setConversationId(resp.conversation_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = {
          role: "assistant",
          text: "Sorry — something went wrong while processing that question. Please try again.",
          isError: true,
        };
        return copy;
      });
    } finally {
      setLoading(false);
    }
  }

  // We generate the conversation id client-side so multi-turn context works
  // from the first follow-up.
  async function sendRequestWithId(question: string) {
    const id = conversationId ?? crypto.randomUUID();
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: id }),
    });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => null);
      throw new Error(detail?.detail || `Request failed (${resp.status})`);
    }
    setConversationId(id);
    return (await resp.json()) as ChatResponse;
  }

  function reset() {
    setMessages([]);
    setConversationId(null);
    setError(null);
  }

  const showSuggestions = messages.length === 0;

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col px-4">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-[var(--artha-border)] py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Artha</h1>
          <p className="text-xs text-[var(--artha-muted)]">
            AI Finance Assistant — every answer grounded in your financial data
          </p>
        </div>
        {messages.length > 0 && (
          <button
            onClick={reset}
            className="rounded-md border border-[var(--artha-border)] px-3 py-1.5 text-xs text-[var(--artha-muted)] hover:bg-black/5"
          >
            New conversation
          </button>
        )}
      </header>

      {/* Messages */}
      <main className="flex-1 space-y-4 overflow-y-auto py-4">
        {showSuggestions && (
          <div className="rounded-xl border border-[var(--artha-border)] bg-[var(--artha-panel)] p-5">
            <h2 className="text-sm font-medium">Ask about your finances</h2>
            <p className="mt-1 text-xs text-[var(--artha-muted)]">
              Vendor payouts, spend, reconciliation — answers computed directly
              from the database.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => ask(q)}
                  className="rounded-full border border-[var(--artha-border)] px-3 py-1.5 text-xs hover:border-[var(--artha-accent)] hover:text-[var(--artha-accent)]"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i}>
            {msg.role === "user" ? (
              <div className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-[var(--artha-accent)] px-4 py-2.5 text-sm text-white">
                  {msg.text}
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-start">
                {loading && i === messages.length - 1 && msg.text === "" ? (
                  <div className="flex items-center gap-2 rounded-2xl rounded-bl-sm border border-[var(--artha-border)] bg-[var(--artha-panel)] px-4 py-3 text-sm text-[var(--artha-muted)]">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--artha-accent)] [animation-delay:-0.2s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--artha-accent)] [animation-delay:-0.1s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--artha-accent)]" />
                    <span className="ml-1">Querying the financial database…</span>
                  </div>
                ) : (
                  <div
                    className={`max-w-[95%] rounded-2xl rounded-bl-sm border px-4 py-3 text-sm ${
                      msg.isError
                        ? "border-red-300 bg-red-50 text-red-800"
                        : msg.refusal
                          ? "border-amber-300 bg-amber-50 text-amber-900"
                          : "border-[var(--artha-border)] bg-[var(--artha-panel)]"
                    }`}
                  >
                    <div className="whitespace-pre-wrap">{msg.text}</div>
                    {msg.suggestions && msg.suggestions.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {msg.suggestions.map((s) => (
                          <button
                            key={s}
                            onClick={() => ask(s)}
                            className="rounded-full border border-current/30 px-3 py-1 text-xs hover:bg-black/5"
                          >
                            {s}
                          </button>
                        ))}
                      </div>
                    )}
                    {msg.evidence && <EvidencePanel evidence={msg.evidence} />}
                    {msg.meta?.model && (
                      <div className="mt-2 text-[10px] text-[var(--artha-muted)]">
                        {msg.meta.provider} · {msg.meta.model}
                        {msg.meta.understanding_latency_ms != null &&
                          ` · ${msg.meta.understanding_latency_ms}ms`}
                        {msg.meta.grounded ? " · grounded" : ""}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </main>

      {/* Input */}
      <footer className="border-t border-[var(--artha-border)] py-3">
        {error && (
          <div className="mb-2 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
          className="flex gap-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your finances…"
            disabled={loading}
            className="flex-1 rounded-xl border border-[var(--artha-border)] bg-[var(--artha-panel)] px-4 py-2.5 text-sm outline-none focus:border-[var(--artha-accent)] disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="rounded-xl bg-[var(--artha-accent)] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40"
          >
            Send
          </button>
        </form>
      </footer>
    </div>
  );
}
