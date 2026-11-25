"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, CircleHelp, LockKeyhole, LogOut, Plus, Send, Sparkles } from "lucide-react";
import { TouchButton } from "@/components/ui";
import { AgentReply, bogotaToday, isAgentReply, suggestedQuestions, TOOL_LABELS } from "@/lib/businessAi";

type Access = "checking" | "locked" | "ready" | "disabled";
type Message = { id: number; role: "user"; text: string } | { id: number; role: "agent"; reply: AgentReply };

async function parseError(response: Response, fallback: string): Promise<string> {
  const data: unknown = await response.json().catch(() => null);
  return data && typeof data === "object" && "error" in data && typeof data.error === "string"
    ? data.error : fallback;
}

export default function BusinessAiPage() {
  const [access, setAccess] = useState<Access>("checking");
  const [accessCode, setAccessCode] = useState("");
  const [accessError, setAccessError] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [chatError, setChatError] = useState("");
  const [today, setToday] = useState("");
  const serial = useRef(0);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setToday(bogotaToday());
    void fetch("/api/business-ai/session", { cache: "no-store", signal: controller.signal })
      .then(response => response.json())
      .then(data => setAccess(data.enabled ? data.authenticated ? "ready" : "locked" : "disabled"))
      .catch(() => { if (!controller.signal.aborted) setAccess("disabled"); });
    return () => { controller.abort(); pending.current?.abort(); };
  }, []);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy, chatError]);

  async function unlock(event: FormEvent) {
    event.preventDefault();
    if (unlocking) return;
    setUnlocking(true);
    setAccessError("");
    try {
      const response = await fetch("/api/business-ai/session", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: accessCode }), cache: "no-store",
      });
      if (!response.ok) throw new Error(await parseError(response, "No se pudo validar el acceso."));
      setAccessCode("");
      setAccess("ready");
    } catch (error) {
      setAccessError(error instanceof Error ? error.message : "No se pudo validar el acceso.");
    } finally {
      setUnlocking(false);
    }
  }

  function newConversation() {
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setMessages([]);
    setQuestion("");
    setChatError("");
    setBusy(false);
  }

  async function closeAccess() {
    newConversation();
    try {
      const response = await fetch("/api/business-ai/session", { method: "DELETE", cache: "no-store" });
      if (!response.ok) throw new Error("No se pudo cerrar el acceso. Intenta nuevamente.");
      setAccess("locked");
    } catch {
      setChatError("No se pudo cerrar el acceso. Intenta nuevamente.");
    }
  }

  async function send(text: string) {
    const clean = text.trim();
    if (access !== "ready" || busy || clean.length < 3 || clean.length > 600) return;
    const run = ++generation.current;
    const controller = new AbortController();
    pending.current = controller;
    setMessages(items => [...items, { id: ++serial.current, role: "user", text: clean }]);
    setQuestion("");
    setChatError("");
    setBusy(true);
    try {
      const response = await fetch("/api/business-ai/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: clean }), signal: controller.signal, cache: "no-store",
      });
      if (!response.ok) {
        if (response.status === 401) setAccess("locked");
        throw new Error(await parseError(response, "No se pudo completar la consulta."));
      }
      const reply: unknown = await response.json();
      if (!isAgentReply(reply)) throw new Error("La respuesta del agente no tiene el formato esperado.");
      if (run === generation.current) setMessages(items => [...items, { id: ++serial.current, role: "agent", reply }]);
    } catch (error) {
      if (run === generation.current && !controller.signal.aborted) {
        setChatError(error instanceof Error ? error.message : "No se pudo completar la consulta.");
      }
    } finally {
      if (run === generation.current) { setBusy(false); pending.current = null; }
    }
  }

  const suggestions = suggestedQuestions(today || "2026-11-25");

  return (
    <main className="ai-page">
      <div className="ai-heading">
        <div className="ai-heading__identity"><span className="ai-heading__icon"><Sparkles size={28} aria-hidden="true" /></span><div><p className="ai-eyebrow">ASISTENTE EXPERIMENTAL</p><h1>Comprefácil AI</h1><p>Preguntas claras sobre los datos de tu negocio.</p></div></div>
        {access === "ready" && <div className="ai-heading__actions">
          <TouchButton variant="secondary" size="md" onClick={newConversation}><Plus size={20} aria-hidden="true" />Nueva conversación</TouchButton>
          <TouchButton variant="ghost" size="md" onClick={() => void closeAccess()}><LogOut size={20} aria-hidden="true" />Cerrar acceso</TouchButton>
        </div>}
      </div>

      {access === "checking" && <div className="ai-access" role="status">Verificando acceso a Comprefácil AI…</div>}
      {access === "disabled" && <div className="ai-access" role="status"><CircleHelp size={26} aria-hidden="true" /><h2>Comprefácil AI no está disponible</h2><p>La función experimental debe habilitarse en el servidor.</p></div>}
      {access === "locked" && <section className="ai-access" aria-label="Acceso a Comprefácil AI">
        <LockKeyhole size={30} aria-hidden="true" /><h2>Acceso experimental</h2>
        <p>Ingresa el código de acceso de esta interfaz. Es distinto de las credenciales del agente y no se guarda en el navegador.</p>
        <form onSubmit={unlock} className="ai-access__form"><label htmlFor="ai-code">Código de acceso</label><input id="ai-code" type="password" autoComplete="off" value={accessCode} onChange={e => setAccessCode(e.target.value)} minLength={32} maxLength={200} required />
          <TouchButton type="submit" loading={unlocking}>Ingresar <ArrowRight size={20} aria-hidden="true" /></TouchButton></form>
        {accessError && <p className="ai-error" role="alert"><AlertCircle size={18} aria-hidden="true" />{accessError}</p>}
      </section>}

      {access === "ready" && <div className="ai-workspace">
        <section className="ai-prompts" aria-label="Consultas rápidas">
          <p className="ai-eyebrow">PARA EMPEZAR</p><h2>¿Qué quieres saber?</h2><p>Estas consultas usan la fecha comercial de Bogotá.</p>
          <div className="ai-prompts__list">{suggestions.map(item => <button key={item.title} type="button" onClick={() => void send(item.question)} disabled={busy} className="ai-prompt"><span>{item.title}</span><small>{item.question}</small><ArrowRight size={18} aria-hidden="true" /></button>)}</div>
          <p className="ai-prompts__note"><CircleHelp size={17} aria-hidden="true" />Cada pregunta se analiza por separado. El historial visible solo dura mientras esta página esté abierta.</p>
        </section>
        <section className="ai-chat" aria-label="Conversación">
          <div className="ai-chat__top"><div><h2>Conversación</h2><p>Datos consultados mediante herramientas de solo lectura</p></div><span className="ai-chat__status">Experimental</span></div>
          <div className="ai-chat__history" aria-live="polite" aria-relevant="additions">
            {messages.length === 0 && <div className="ai-empty"><Sparkles size={34} aria-hidden="true" /><h3>Una pregunta, una respuesta con respaldo</h3><p>Prueba una consulta rápida o escribe una pregunta sobre ventas, rentabilidad, inventario o compras.</p></div>}
            {messages.map(message => message.role === "user"
              ? <div key={message.id} className="ai-message ai-message--user"><span className="ai-message__who">Tú</span><p>{message.text}</p></div>
              : <div key={message.id} className="ai-message ai-message--agent"><span className="ai-message__who"><Sparkles size={15} aria-hidden="true" />Comprefácil AI</span><p className="ai-message__answer">{message.reply.answer}</p>
                {message.reply.tools_used.length > 0 && <div className="ai-message__sources"><strong>Consultó</strong> {Array.from(new Set(message.reply.tools_used)).map(name => TOOL_LABELS[name]).join(" · ")}</div>}
                {message.reply.periods.length > 0 && <div className="ai-message__sources"><strong>Período</strong> {message.reply.periods.map(p => `${p.date_from} a ${p.date_to} (${p.timezone})`).join(" · ")}</div>}
                {message.reply.limitations.length > 0 && <div className="ai-message__limits"><strong>Limitaciones de los datos</strong><ul>{message.reply.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
              </div>)}
            {busy && <div className="ai-thinking" role="status"><span className="ai-thinking__dots" aria-hidden="true">•••</span>Consultando datos del negocio…</div>}
            {chatError && <div className="ai-error" role="alert"><AlertCircle size={18} aria-hidden="true" />{chatError}</div>}
            <div ref={end} />
          </div>
          <form className="ai-composer" onSubmit={event => { event.preventDefault(); void send(question); }}>
            <label className="ui-sr-only" htmlFor="ai-question">Escribe tu pregunta</label>
            <textarea id="ai-question" placeholder="Pregunta sobre tu negocio…" rows={2} value={question} maxLength={600} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(question); } }} disabled={busy} />
            <TouchButton type="submit" iconOnly icon={Send} label="Enviar pregunta" disabled={busy || question.trim().length < 3} />
            <p>Solo consultas. No realiza ventas, compras ni cambios en el sistema.</p>
          </form>
        </section>
      </div>}
    </main>
  );
}
