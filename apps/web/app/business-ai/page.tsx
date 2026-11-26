"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, Bell, CalendarClock, CircleCheck, CircleHelp, LockKeyhole, LogOut, Plus, RotateCw, Send, Sparkles, X } from "lucide-react";
import { ConfirmDialog, TouchButton } from "@/components/ui";
import { AgentReply, bogotaToday, isAgentReply, suggestedQuestions, TOOL_LABELS } from "@/lib/businessAi";
import { ALERT_LABELS, attentionAnswer, isAttentionQuestion, isNotificationCenter,
  isReminderRequest, NotificationCenter, PersonalReminder, PRIORITY_LABELS, reminderCommand,
  ReminderDraft } from "@/lib/businessNotifications";

type Access = "checking" | "locked" | "ready" | "disabled";
type Message = { id: number; role: "user"; text: string } | { id: number; role: "agent"; reply: AgentReply };
type PreparedAction = { action: "create"; draft: ReminderDraft; token: string }
  | { action: "complete" | "cancel"; reminder: PersonalReminder; token: string };

function localReply(answer: string): AgentReply {
  return { answer, tools_used: [], periods: [], limitations: [],
    usage: { model_calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 }, duration_ms: 0 };
}

function dueLabel(value: string) {
  return new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

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
  const [resetting, setResetting] = useState(false);
  const [chatError, setChatError] = useState("");
  const [today, setToday] = useState("");
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [center, setCenter] = useState<NotificationCenter | null>(null);
  const [centerLoading, setCenterLoading] = useState(false);
  const [centerError, setCenterError] = useState("");
  const [reminderTitle, setReminderTitle] = useState("");
  const [reminderDue, setReminderDue] = useState("");
  const [prepared, setPrepared] = useState<PreparedAction | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const serial = useRef(0);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setToday(bogotaToday());
    void fetch("/api/business-ai/session", { cache: "no-store", signal: controller.signal })
      .then(response => response.json())
      .then(data => { setAccess(data.enabled ? data.authenticated ? "ready" : "locked" : "disabled");
        setNotificationsEnabled(data.notifications_enabled === true); })
      .catch(() => { if (!controller.signal.aborted) setAccess("disabled"); });
    return () => { controller.abort(); pending.current?.abort(); };
  }, []);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy, chatError]);

  const loadCenter = useCallback(async (): Promise<NotificationCenter> => {
    setCenterLoading(true);
    setCenterError("");
    try {
      const response = await fetch("/api/business-ai/notifications", { cache: "no-store" });
      if (!response.ok) throw new Error(await parseError(response, "No se pudieron consultar las notificaciones."));
      const data: unknown = await response.json();
      if (!isNotificationCenter(data)) throw new Error("La respuesta del centro no tiene el formato esperado.");
      setCenter(data);
      return data;
    } catch (error) {
      const message = error instanceof Error ? error.message : "No se pudieron consultar las notificaciones.";
      setCenterError(message);
      throw error;
    } finally { setCenterLoading(false); }
  }, []);

  useEffect(() => {
    if (access !== "ready" || !notificationsEnabled) return;
    void loadCenter().catch(() => undefined);
    const timer = setInterval(() => void loadCenter().catch(() => undefined), 60_000);
    return () => clearInterval(timer);
  }, [access, notificationsEnabled, loadCenter]);

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
      const data: unknown = await response.json();
      setAccessCode("");
      setNotificationsEnabled(!!data && typeof data === "object" && "notifications_enabled" in data
        && data.notifications_enabled === true);
      setAccess("ready");
    } catch (error) {
      setAccessError(error instanceof Error ? error.message : "No se pudo validar el acceso.");
    } finally {
      setUnlocking(false);
    }
  }

  function clearConversation() {
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setMessages([]);
    setQuestion("");
    setChatError("");
    setBusy(false);
  }

  async function newConversation() {
    if (resetting) return;
    setResetting(true);
    generation.current += 1;
    pending.current?.abort();
    pending.current = null;
    setBusy(false);
    try {
      const response = await fetch("/api/business-ai/session", { method: "PATCH", cache: "no-store" });
      if (!response.ok) throw new Error(await parseError(response, "No se pudo iniciar una conversación nueva."));
      const data: unknown = await response.json();
      setNotificationsEnabled(!!data && typeof data === "object" && "notifications_enabled" in data
        && data.notifications_enabled === true);
      clearConversation();
    } catch (error) {
      setChatError(error instanceof Error ? error.message : "No se pudo iniciar una conversación nueva.");
    } finally {
      setResetting(false);
    }
  }

  async function closeAccess() {
    clearConversation();
    try {
      const response = await fetch("/api/business-ai/session", { method: "DELETE", cache: "no-store" });
      if (!response.ok) throw new Error("No se pudo cerrar el acceso. Intenta nuevamente.");
      setAccess("locked");
      setNotificationsEnabled(false);
      setCenter(null);
    } catch {
      setChatError("No se pudo cerrar el acceso. Intenta nuevamente.");
    }
  }

  async function send(text: string) {
    const clean = text.trim();
    if (access !== "ready" || busy || resetting || clean.length < 3 || clean.length > 600) return;
    const run = ++generation.current;
    const controller = new AbortController();
    pending.current = controller;
    setMessages(items => [...items, { id: ++serial.current, role: "user", text: clean }]);
    setQuestion("");
    setChatError("");
    setBusy(true);
    try {
      if (notificationsEnabled && isReminderRequest(clean)) {
        const parsed = reminderCommand(clean);
        if (parsed) { setReminderTitle(parsed.title); setReminderDue(parsed.due_local); }
        else { setReminderTitle(clean.replace(/^\s*recu[eé]rdame\s*/i, "").slice(0, 240)); setReminderDue(""); }
        const answer = parsed
          ? "Preparé el recordatorio en el centro de notificaciones. Revisa la fecha y confirma antes de crearlo."
          : "Abre el formulario del centro, indica fecha y hora de Bogotá y confirma el recordatorio.";
        setMessages(items => [...items, { id: ++serial.current, role: "agent", reply: localReply(answer) }]);
        return;
      }
      if (notificationsEnabled && isAttentionQuestion(clean)) {
        const snapshot = await loadCenter();
        setMessages(items => [...items, { id: ++serial.current, role: "agent",
          reply: localReply(attentionAnswer(snapshot, clean)) }]);
        return;
      }
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

  async function notificationPost(body: object): Promise<Record<string, unknown>> {
    const response = await fetch("/api/business-ai/notifications", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), cache: "no-store",
    });
    if (!response.ok) throw new Error(await parseError(response, "No se pudo procesar el recordatorio."));
    const data: unknown = await response.json();
    if (!data || typeof data !== "object") throw new Error("Respuesta de recordatorio inválida.");
    return data as Record<string, unknown>;
  }

  async function prepareCreate(event: FormEvent) {
    event.preventDefault();
    if (actionBusy || !reminderTitle.trim() || !reminderDue) return;
    setActionBusy(true);
    setCenterError("");
    try {
      const draft: ReminderDraft = { title: reminderTitle.trim(), due_local: reminderDue,
        idempotency_key: crypto.randomUUID() };
      const result = await notificationPost({ operation: "intent", action: "create", draft });
      if (typeof result.approval_token !== "string") throw new Error("Confirmación inválida.");
      setPrepared({ action: "create", draft, token: result.approval_token });
    } catch (error) { setCenterError(error instanceof Error ? error.message : "No se pudo preparar el recordatorio."); }
    finally { setActionBusy(false); }
  }

  async function prepareResolution(reminder: PersonalReminder, action: "complete" | "cancel") {
    if (actionBusy) return;
    setActionBusy(true);
    setCenterError("");
    try {
      const result = await notificationPost({ operation: "intent", action, reminder_id: reminder.id });
      if (typeof result.approval_token !== "string") throw new Error("Confirmación inválida.");
      setPrepared({ action, reminder, token: result.approval_token });
    } catch (error) { setCenterError(error instanceof Error ? error.message : "No se pudo preparar la acción."); }
    finally { setActionBusy(false); }
  }

  async function confirmAction() {
    if (!prepared || actionBusy) return;
    setActionBusy(true);
    setCenterError("");
    try {
      if (prepared.action === "create") {
        await notificationPost({ operation: "create", draft: prepared.draft, approval_token: prepared.token });
        setReminderTitle(""); setReminderDue("");
      } else {
        await notificationPost({ operation: "resolve", action: prepared.action,
          reminder_id: prepared.reminder.id, approval_token: prepared.token });
      }
      setPrepared(null);
      await loadCenter();
    } catch (error) { setCenterError(error instanceof Error ? error.message : "No se pudo guardar el recordatorio.");
      setPrepared(null); }
    finally { setActionBusy(false); }
  }

  const suggestions = suggestedQuestions(today || "2026-11-25");

  return (
    <main className="ai-page">
      <div className="ai-heading">
        <div className="ai-heading__identity"><span className="ai-heading__icon"><Sparkles size={28} aria-hidden="true" /></span><div><p className="ai-eyebrow">ASISTENTE EXPERIMENTAL</p><h1>Comprefácil AI</h1><p>Preguntas claras sobre los datos de tu negocio.</p></div></div>
        {access === "ready" && <div className="ai-heading__actions">
          <TouchButton variant="secondary" size="md" onClick={() => void newConversation()} disabled={resetting}><Plus size={20} aria-hidden="true" />Nueva conversación</TouchButton>
          <TouchButton variant="ghost" size="md" onClick={() => void closeAccess()} disabled={resetting}><LogOut size={20} aria-hidden="true" />Cerrar acceso</TouchButton>
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
        <div className="ai-sidebar">
        <section className="ai-prompts" aria-label="Consultas rápidas">
          <p className="ai-eyebrow">PARA EMPEZAR</p><h2>¿Qué quieres saber?</h2><p>Estas consultas usan la fecha comercial de Bogotá.</p>
          <div className="ai-prompts__list">{suggestions.map(item => <button key={item.title} type="button" onClick={() => void send(item.question)} disabled={busy || resetting} className="ai-prompt"><span>{item.title}</span><small>{item.question}</small><ArrowRight size={18} aria-hidden="true" /></button>)}</div>
          <p className="ai-prompts__note"><CircleHelp size={17} aria-hidden="true" />Puedes hacer preguntas de seguimiento. El contexto vence tras 30 minutos de inactividad. Al recargar, el historial visible desaparece aunque el contexto aún puede existir. Nueva conversación lo reinicia.</p>
        </section>
        {notificationsEnabled && <section className="ai-notifications" aria-label="Centro de notificaciones">
          <div className="ai-notifications__head"><div><p className="ai-eyebrow">ATENCIÓN COMERCIAL</p><h2><Bell size={21} aria-hidden="true" />Alertas y recordatorios</h2></div>
            <TouchButton variant="ghost" iconOnly icon={RotateCw} label="Actualizar alertas" disabled={centerLoading} onClick={() => void loadCenter().catch(() => undefined)} /></div>
          {centerLoading && !center && <p role="status">Cargando pendientes…</p>}
          {centerError && <p className="ai-error" role="alert"><AlertCircle size={18} aria-hidden="true" />{centerError}</p>}
          {center && <>
            <div className="ai-notifications__group"><h3>Alertas activas <span>{center.alerts.length}</span></h3>
              {center.alerts.length === 0 && <p className="ai-notifications__empty">Sin alertas en el período consultado.</p>}
              <ul>{center.alerts.map(alert => <li key={alert.id} className="ai-notification" data-priority={alert.priority}>
                <div className="ai-notification__tags"><span>{ALERT_LABELS[alert.category]}</span><strong>{PRIORITY_LABELS[alert.priority]}</strong></div>
                <h4>{alert.title}</h4><p>{alert.detail}</p><a href={alert.href}>Abrir módulo <ArrowRight size={16} aria-hidden="true" /></a>
              </li>)}</ul>
              {center.alerts_truncated && <p className="ai-notifications__empty">La lista está limitada; consulta el módulo correspondiente para más detalles.</p>}
            </div>
            <div className="ai-notifications__group"><h3>Recordatorios pendientes <span>{center.reminders.length}</span></h3>
              {center.reminders.length === 0 && <p className="ai-notifications__empty">No tienes recordatorios pendientes.</p>}
              <ul>{center.reminders.map(reminder => <li key={reminder.id} className="ai-notification">
                <div className="ai-notification__tags"><span>Personal</span><strong>{new Date(reminder.due_at) < new Date() ? "Vencido" : "Pendiente"}</strong></div>
                <h4>{reminder.title}</h4><p><CalendarClock size={16} aria-hidden="true" />{dueLabel(reminder.due_at)}</p>
                <div className="ai-notification__actions"><TouchButton variant="secondary" size="md" onClick={() => void prepareResolution(reminder, "complete")} disabled={actionBusy}><CircleCheck size={16} aria-hidden="true" />Completar</TouchButton>
                  <TouchButton variant="ghost" size="md" onClick={() => void prepareResolution(reminder, "cancel")} disabled={actionBusy}><X size={16} aria-hidden="true" />Cancelar</TouchButton></div>
              </li>)}</ul>
              {center.reminders_truncated && <p className="ai-notifications__empty">Hay más recordatorios pendientes; la lista muestra los próximos 100.</p>}
            </div>
          </>}
          <form className="ai-reminder-form" onSubmit={event => void prepareCreate(event)}>
            <h3>Nuevo recordatorio</h3>
            <label htmlFor="ai-reminder-title">¿Qué quieres recordar?</label>
            <input id="ai-reminder-title" value={reminderTitle} onChange={event => setReminderTitle(event.target.value)} minLength={3} maxLength={240} required placeholder="Revisar pedido de bebidas" />
            <label htmlFor="ai-reminder-due">Fecha y hora · Bogotá</label>
            <input id="ai-reminder-due" type="datetime-local" value={reminderDue} onChange={event => setReminderDue(event.target.value)} required />
            <TouchButton type="submit" size="md" disabled={actionBusy || reminderTitle.trim().length < 3 || !reminderDue}>Revisar y crear</TouchButton>
          </form>
        </section>}
        </div>
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
            <textarea id="ai-question" placeholder="Pregunta sobre tu negocio…" rows={2} value={question} maxLength={600} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(question); } }} disabled={busy || resetting} />
            <TouchButton type="submit" iconOnly icon={Send} label="Enviar pregunta" disabled={busy || resetting || question.trim().length < 3} />
            <p>Consultas y recordatorios con confirmación. No modifica ventas, inventario, caja ni compras.</p>
          </form>
        </section>
      </div>}
      <ConfirmDialog open={!!prepared} title={prepared?.action === "create" ? "Crear recordatorio" : prepared?.action === "cancel" ? "Cancelar recordatorio" : "Completar recordatorio"}
        confirmLabel={prepared?.action === "create" ? "Crear recordatorio" : prepared?.action === "cancel" ? "Cancelar recordatorio" : "Completar recordatorio"}
        tone={prepared?.action === "cancel" ? "danger" : "primary"} busy={actionBusy}
        onConfirm={() => void confirmAction()} onCancel={() => setPrepared(null)}>
        {prepared?.action === "create" ? <p>¿Crear “{prepared.draft.title}” para el {dueLabel(`${prepared.draft.due_local}:00-05:00`)} (Bogotá)?</p>
          : prepared ? <p>¿{prepared.action === "cancel" ? "Cancelar" : "Completar"} “{prepared.reminder.title}”?</p> : null}
      </ConfirmDialog>
    </main>
  );
}
