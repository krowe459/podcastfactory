"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ClipboardPaste,
  Download,
  FileText,
  FileUp,
  Headphones,
  Loader2,
  RotateCcw,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import {
  STATUS_VALUES,
  isInProgress,
  isStatus,
  type CompleteData,
  type ErrorData,
  type Status,
  type StreamEvent,
} from "@/lib/contracts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".txt", ".md"];
const MAX_UPLOAD_MB = 25;
const DURATIONS = [5, 10, 15, 20, 30] as const;

// Status strings come only from lib/contracts.ts.
const [EXTRACTING_FACTS, GENERATING_SCRIPT, REVISING_OUTLINE, SYNTHESIZING_AUDIO, COMPLETE, ERROR] =
  STATUS_VALUES;

type UiStatus = Status | "idle";

const BUTTON_LABELS = {
  idle: "Generate Podcast",
  [EXTRACTING_FACTS]: "Analyzing the reading (Agent 1 active)...",
  [GENERATING_SCRIPT]: "Writing the dialogue (Agents 2 & 3 active)...",
  [REVISING_OUTLINE]: "Adjusting length (Agent 1 active)...",
  [SYNTHESIZING_AUDIO]: "Producing audio (Agent 4 active)...",
  [COMPLETE]: "Generate Another Podcast",
  [ERROR]: "Try Again",
} as const satisfies Record<UiStatus, string>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
}

function validateFile(file: File): string | null {
  const ext = extensionOf(file.name);
  if (!ACCEPTED_EXTENSIONS.includes(ext)) {
    return `"${file.name}" is not supported. Upload a PDF, DOCX, PPTX, TXT, or MD file.`;
  }
  if (file.size === 0) return `"${file.name}" is empty.`;
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `"${file.name}" is larger than ${MAX_UPLOAD_MB} MB.`;
  return null;
}

function formatMinutes(minutes: number): string {
  const m = Math.floor(minutes);
  const s = Math.round((minutes - m) * 60);
  return s === 60 ? `${m + 1}:00` : `${m}:${s.toString().padStart(2, "0")}`;
}

/** Parse one SSE block ("data: {...}" lines). Returns null for comments/keep-alives. */
function parseEventBlock(block: string): StreamEvent | null {
  const dataLines = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart());
  if (dataLines.length === 0) return null;
  const parsed: unknown = JSON.parse(dataLines.join("\n"));
  if (!parsed || typeof parsed !== "object") return null;
  const candidate = parsed as Partial<StreamEvent>;
  if (!isStatus(candidate.status)) return null;
  return {
    status: candidate.status,
    message: typeof candidate.message === "string" ? candidate.message : "",
    progress: typeof candidate.progress === "number" ? candidate.progress : 0,
    data: candidate.data && typeof candidate.data === "object" ? candidate.data : null,
  };
}

// ---------------------------------------------------------------------------
// Small presentational pieces
// ---------------------------------------------------------------------------

function Waveform({ seed }: { seed: string }) {
  const bars = useMemo(() => {
    let h = 2166136261;
    for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    const out: number[] = [];
    for (let i = 0; i < 72; i++) {
      h = Math.imul(h ^ (h >>> 15), 2246822507) ^ i;
      const noise = ((h >>> 0) % 1000) / 1000;
      const envelope = 0.45 + 0.55 * Math.sin((i / 72) * Math.PI);
      out.push(Math.max(0.12, envelope * (0.35 + 0.65 * noise)));
    }
    return out;
  }, [seed]);
  return (
    <svg viewBox="0 0 288 56" className="h-14 w-full" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="wave" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="#818cf8" />
          <stop offset="100%" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      {bars.map((v, i) => {
        const height = v * 52;
        return (
          <rect key={i} x={i * 4 + 0.5} y={(56 - height) / 2} width={2.5} height={height} rx={1.25} fill="url(#wave)" />
        );
      })}
    </svg>
  );
}

function TranscriptView({ markdown }: { markdown: string }) {
  const lines = markdown.split("\n");
  return (
    <div className="space-y-2 text-sm leading-relaxed text-slate-300">
      {lines.map((line, i) => {
        if (!line.trim()) return null;
        if (line.startsWith("# ")) return <h3 key={i} className="pt-1 text-lg font-semibold text-white">{line.slice(2)}</h3>;
        if (line.startsWith("## ")) return <h4 key={i} className="pt-4 text-base font-semibold text-indigo-200">{line.slice(3)}</h4>;
        if (line === "---") return <hr key={i} className="border-slate-800" />;
        const host = line.match(/^(Host [AB](?: \([^)]*\))?):\s(.*)$/);
        if (host) {
          const isA = host[1].startsWith("Host A");
          return (
            <p key={i}>
              <span className={`font-semibold ${isA ? "text-indigo-300" : "text-cyan-300"}`}>{host[1]}:</span>{" "}
              {host[2]}
            </p>
          );
        }
        if (line.startsWith("- ")) return <p key={i} className="pl-4 text-xs text-slate-400">• {line.slice(2)}</p>;
        const cleaned = line.replace(/\*\*(.*?)\*\*/g, "$1").replace(/^_(.*)_$/, "$1").replace(/\s{2}$/, "");
        return <p key={i} className="text-slate-400">{cleaned}</p>;
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Page() {
  // Inputs
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [pasteMode, setPasteMode] = useState(false);
  const [pastedText, setPastedText] = useState("");
  const [focus, setFocus] = useState("");
  const [minutes, setMinutes] = useState<number>(10);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Run state
  const [status, setStatus] = useState<UiStatus>("idle");
  const [message, setMessage] = useState("");
  const [progress, setProgress] = useState(0);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [result, setResult] = useState<CompleteData | null>(null);
  const [submittedMinutes, setSubmittedMinutes] = useState<number>(10);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const busy = isInProgress(status === "idle" ? null : status);
  const hasInput = pasteMode ? pastedText.trim().length > 0 : file !== null;

  useEffect(() => () => abortRef.current?.abort(), []);

  // ----------------------------------------------------------- file handling

  const acceptFile = useCallback((candidate: File | undefined | null) => {
    if (!candidate) return;
    const problem = validateFile(candidate);
    if (problem) {
      setFileError(problem);
      setFile(null);
      return;
    }
    setFileError(null);
    setFile(candidate);
  }, []);

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (busy) return;
    if (e.dataTransfer.files.length > 1) {
      setFileError("Drop one reading at a time.");
      return;
    }
    acceptFile(e.dataTransfer.files[0]);
  };

  const removeFile = () => {
    setFile(null);
    setFileError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // ----------------------------------------------------------------- stream

  const failWith = (detail: string) => {
    setStatus(ERROR);
    setErrorDetail(detail);
    setMessage(detail);
    setProgress(100);
  };

  const handleEvent = (event: StreamEvent) => {
    setStatus(event.status);
    setMessage(event.message);
    setProgress(Math.max(0, Math.min(100, event.progress)));
    if (event.status === COMPLETE) {
      setResult(event.data as unknown as CompleteData);
    } else if (event.status === ERROR) {
      const detail = (event.data as unknown as ErrorData | null)?.detail ?? event.message;
      setErrorDetail(detail || "The podcast could not be produced.");
    }
  };

  const generate = async () => {
    if (!hasInput) {
      setFileError(pasteMode ? "Paste the reading's text first." : "Choose a reading to upload first.");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setResult(null);
    setTranscript(null);
    setTranscriptError(null);
    setTranscriptOpen(false);
    setErrorDetail(null);
    setSubmittedMinutes(minutes);
    setStatus(EXTRACTING_FACTS);
    setMessage("Uploading the reading...");
    setProgress(1);

    const form = new FormData();
    if (pasteMode) form.append("text", pastedText);
    else if (file) form.append("file", file, file.name);
    if (focus.trim()) form.append("topic_focus", focus.trim());
    form.append("target_minutes", String(minutes));

    let terminal = false;
    try {
      const response = await fetch(`${API_BASE}/api/generate-podcast`, {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        failWith(`The server responded with ${response.status} ${response.statusText}.`);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const drain = (final: boolean) => {
        buffer = buffer.replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseEventBlock(block);
          if (event) {
            if (event.status === COMPLETE || event.status === ERROR) terminal = true;
            handleEvent(event);
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (final && buffer.trim()) {
          const event = parseEventBlock(buffer);
          buffer = "";
          if (event) {
            if (event.status === COMPLETE || event.status === ERROR) terminal = true;
            handleEvent(event);
          }
        }
      };
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        drain(false);
      }
      buffer += decoder.decode();
      drain(true);
      if (!terminal) failWith("The connection closed before the podcast was finished. Please try again.");
    } catch (err) {
      if (controller.signal.aborted) return;
      const reason = err instanceof Error ? err.message : String(err);
      failWith(
        `Could not reach the PodcastFactory server at ${API_BASE}. Is the backend running? (${reason})`,
      );
    }
  };

  const resetForAnother = () => {
    setStatus("idle");
    setMessage("");
    setProgress(0);
    setResult(null);
    setTranscript(null);
    setErrorDetail(null);
    removeFile();
    setPastedText("");
  };

  const onAction = () => {
    if (busy) return;
    if (status === COMPLETE) resetForAnother();
    else void generate();
  };

  // ------------------------------------------------------------- transcript

  useEffect(() => {
    if (!result) return;
    let cancelled = false;
    fetch(`${API_BASE}${result.transcript_url}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.text();
      })
      .then((text) => !cancelled && setTranscript(text))
      .catch((e) => !cancelled && setTranscriptError(`Could not load the transcript (${e.message}).`));
    return () => {
      cancelled = true;
    };
  }, [result]);

  // ----------------------------------------------------------------- render

  const coverage = result?.coverage;
  const allCovered = coverage ? coverage.must_cover_hit === coverage.must_cover_total : false;
  const durationOff = coverage ? Math.abs(coverage.estimated_minutes - submittedMinutes) / submittedMinutes > 0.1 : false;

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:py-14">
      <header className="mb-8">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-500/15 ring-1 ring-indigo-400/30">
            <Headphones className="h-5 w-5 text-indigo-300" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-white">PodcastFactory</h1>
            <p className="text-sm text-slate-400">Turn an assigned reading into a two-host podcast you can listen to.</p>
          </div>
        </div>
      </header>

      <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 shadow-xl shadow-black/30 sm:p-6">
        {/* Source input */}
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-200">1. The reading</h2>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setPasteMode((p) => !p);
              setFileError(null);
            }}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-50"
          >
            {pasteMode ? <FileUp className="h-3.5 w-3.5" /> : <ClipboardPaste className="h-3.5 w-3.5" />}
            {pasteMode ? "Upload a file instead" : "Paste text instead"}
          </button>
        </div>

        {pasteMode ? (
          <textarea
            value={pastedText}
            onChange={(e) => setPastedText(e.target.value)}
            disabled={busy}
            rows={9}
            placeholder="Paste the full text of the reading here..."
            className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950/60 p-4 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 disabled:opacity-60"
          />
        ) : (
          <div
            role="button"
            tabIndex={0}
            aria-label="Upload a reading"
            onClick={() => !busy && !file && fileInputRef.current?.click()}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && !busy && !file) {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            onDragEnter={(e) => {
              e.preventDefault();
              if (!busy) setDragActive(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragActive(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              if (e.currentTarget.contains(e.relatedTarget as Node)) return;
              setDragActive(false);
            }}
            onDrop={onDrop}
            className={[
              "relative flex min-h-[168px] flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition",
              dragActive
                ? "scale-[1.01] border-indigo-400 bg-indigo-500/10"
                : file
                  ? "border-slate-700 bg-slate-950/40"
                  : "cursor-pointer border-slate-700 bg-slate-950/40 hover:border-slate-500 hover:bg-slate-950/70",
              busy ? "pointer-events-none opacity-60" : "",
            ].join(" ")}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXTENSIONS.join(",")}
              className="hidden"
              onChange={(e) => acceptFile(e.target.files?.[0])}
            />
            {file ? (
              <div className="flex w-full max-w-md items-center gap-3 rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-left">
                <FileText className="h-6 w-6 shrink-0 text-indigo-300" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-100">{file.name}</p>
                  <p className="text-xs text-slate-400">{formatBytes(file.size)}</p>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile();
                  }}
                  disabled={busy}
                  className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white"
                  aria-label="Remove file"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <>
                <Upload className={`mb-3 h-8 w-8 ${dragActive ? "text-indigo-300" : "text-slate-500"}`} />
                <p className="text-sm font-medium text-slate-200">
                  {dragActive ? "Drop the reading here" : "Drag and drop a reading, or click to browse"}
                </p>
                <p className="mt-1 text-xs text-slate-500">Case, article, chapter, or slide deck: PDF, DOCX, PPTX, TXT, MD</p>
              </>
            )}
          </div>
        )}
        {fileError && (
          <p className="mt-2 flex items-start gap-1.5 text-sm text-rose-300" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {fileError}
          </p>
        )}

        {/* Options */}
        <div className="mt-6 grid gap-5 sm:grid-cols-[1fr_auto]">
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-200">2. Focus</span>
            <input
              type="text"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              disabled={busy}
              placeholder="Optional: what should the hosts emphasize? e.g., implications for pricing strategy"
              className="w-full rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 disabled:opacity-60"
            />
          </label>
          <div>
            <span className="mb-2 block text-sm font-medium text-slate-200">3. Length</span>
            <div className="inline-flex rounded-lg border border-slate-700 bg-slate-950/60 p-1" role="radiogroup" aria-label="Target duration">
              {DURATIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={minutes === d}
                  disabled={busy}
                  onClick={() => setMinutes(d)}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-60 ${
                    minutes === d ? "bg-indigo-500 text-white shadow" : "text-slate-300 hover:bg-slate-800"
                  }`}
                >
                  {d}m
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Action */}
        <div className="mt-7">
          <button
            type="button"
            onClick={onAction}
            disabled={busy}
            aria-busy={busy}
            className={[
              "flex w-full items-center justify-center gap-2 rounded-xl px-5 py-3.5 text-sm font-semibold transition",
              busy
                ? "cursor-wait bg-indigo-500/30 text-indigo-100"
                : status === ERROR
                  ? "bg-rose-500 text-white hover:bg-rose-400"
                  : "bg-indigo-500 text-white shadow-lg shadow-indigo-500/20 hover:bg-indigo-400",
            ].join(" ")}
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : status === ERROR ? (
              <RotateCcw className="h-4 w-4" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            {BUTTON_LABELS[status]}
          </button>

          {status !== "idle" && (
            <div className="mt-4" aria-live="polite">
              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    status === ERROR ? "bg-rose-500" : status === COMPLETE ? "bg-emerald-400" : "bg-gradient-to-r from-indigo-500 to-cyan-400"
                  }`}
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="mt-2 flex items-center justify-between gap-3 text-xs text-slate-400">
                <span className="min-w-0 truncate">{message}</span>
                <span className="shrink-0 tabular-nums">{progress}%</span>
              </div>
            </div>
          )}
        </div>

        {/* Error panel */}
        {status === ERROR && errorDetail && (
          <div className="mt-5 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4" role="alert">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-rose-100">The podcast could not be produced</p>
                <p className="mt-1 break-words text-sm text-rose-200/80">{errorDetail}</p>
                <button
                  type="button"
                  onClick={() => void generate()}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-rose-500/20 px-3 py-1.5 text-xs font-semibold text-rose-100 hover:bg-rose-500/30"
                >
                  <RotateCcw className="h-3.5 w-3.5" /> Retry with the same inputs
                </button>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Results */}
      {status === COMPLETE && result && coverage && (
        <section className="mt-6 rounded-2xl border border-slate-800 bg-slate-900/60 p-5 shadow-xl shadow-black/30 sm:p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-base font-semibold text-white">
              <CheckCircle2 className="h-5 w-5 text-emerald-400" /> Your podcast
            </h2>
            {result.tts_provider === "stub" && (
              <span className="rounded-full border border-amber-400/40 bg-amber-400/10 px-3 py-1 text-xs font-medium text-amber-200">
                Prototype audio: silent placeholder voices
              </span>
            )}
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
            <Waveform seed={result.audio_url} />
            <audio controls src={`${API_BASE}${result.audio_url}`} className="mt-3 w-full" preload="metadata" />
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Length</p>
              <p className="mt-1 text-lg font-semibold text-white tabular-nums">
                {formatMinutes(coverage.estimated_minutes)}{" "}
                <span className="text-sm font-normal text-slate-400">estimated vs {submittedMinutes}:00 target</span>
              </p>
              <p className={`mt-1 text-xs ${durationOff ? "text-amber-300" : "text-slate-500"}`}>
                {coverage.total_words.toLocaleString()} words
                {durationOff ? " (outside the 10% tolerance)" : ""}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Coverage</p>
              <p className={`mt-1 text-lg font-semibold ${allCovered ? "text-emerald-300" : "text-amber-300"}`}>
                {coverage.must_cover_hit} of {coverage.must_cover_total} must-cover ideas addressed
              </p>
            </div>
          </div>

          {coverage.missing_ids.length > 0 && (
            <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/5 p-4">
              <p className="text-sm font-medium text-amber-200">Not covered in the episode (read these in the source):</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-100/80">
                {coverage.missing_ids.map((id) => (
                  <li key={id}>{coverage.missing_points[id] ?? id}</li>
                ))}
              </ul>
            </div>
          )}

          {coverage.warnings.length > 0 && (
            <div className="mt-3 rounded-xl border border-slate-700 bg-slate-950/40 p-4">
              <p className="flex items-center gap-1.5 text-sm font-medium text-slate-200">
                <AlertTriangle className="h-4 w-4 text-amber-300" /> Production notes
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-400">
                {coverage.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 rounded-xl border border-slate-800">
            <div className="flex items-center justify-between gap-2 px-4 py-3">
              <button
                type="button"
                onClick={() => setTranscriptOpen((o) => !o)}
                aria-expanded={transcriptOpen}
                className="flex items-center gap-2 text-sm font-medium text-slate-200 hover:text-white"
              >
                <ChevronDown className={`h-4 w-4 transition ${transcriptOpen ? "rotate-180" : ""}`} />
                Transcript
              </button>
              <a
                href={`${API_BASE}${result.transcript_url}?download=1`}
                className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-indigo-300 hover:bg-indigo-500/10"
              >
                <Download className="h-3.5 w-3.5" /> Download (.md)
              </a>
            </div>
            {transcriptOpen && (
              <div className="max-h-[480px] overflow-y-auto border-t border-slate-800 px-4 py-4">
                {transcript ? (
                  <TranscriptView markdown={transcript} />
                ) : transcriptError ? (
                  <p className="text-sm text-rose-300">{transcriptError}</p>
                ) : (
                  <p className="flex items-center gap-2 text-sm text-slate-400">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading transcript...
                  </p>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      <footer className="mt-8 text-center text-xs text-slate-500">
        Prototype. Episodes are AI-generated guides to the reading; check important details against the source.
      </footer>
    </main>
  );
}
