"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  AudioLines,
  BookOpenText,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardPaste,
  Download,
  FileText,
  FileUp,
  Headphones,
  Loader2,
  MessagesSquare,
  Pause,
  Play,
  RotateCcw,
  Scissors,
  Search,
  Sparkles,
  Target,
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
const SPEEDS = [1, 1.25, 1.5, 2] as const;

// Status strings come only from lib/contracts.ts.
const [EXTRACTING_FACTS, GENERATING_SCRIPT, REVISING_OUTLINE, SYNTHESIZING_AUDIO, COMPLETE, ERROR] =
  STATUS_VALUES;

type UiStatus = Status | "idle";

// The action button is driven directly by the stream's current status.
const BUTTON_LABELS = {
  idle: "Generate Podcast",
  [EXTRACTING_FACTS]: "Debating the topic (Agent 1 active)...",
  [GENERATING_SCRIPT]: "Drafting technical script (Agents 2 & 3 active)...",
  [REVISING_OUTLINE]: "Tightening the script to length (Agent 1 active)...",
  [SYNTHESIZING_AUDIO]: "Generating synthetic vocals (Agent 4 active)...",
  [COMPLETE]: "Generate Another Podcast",
  [ERROR]: "Try Again",
} as const satisfies Record<UiStatus, string>;

const BUTTON_ICONS = {
  idle: Sparkles,
  [EXTRACTING_FACTS]: Search,
  [GENERATING_SCRIPT]: MessagesSquare,
  [REVISING_OUTLINE]: Scissors,
  [SYNTHESIZING_AUDIO]: AudioLines,
  [COMPLETE]: Sparkles,
  [ERROR]: RotateCcw,
} as const satisfies Record<UiStatus, React.ComponentType<{ className?: string }>>;

// The pipeline rail on the right. Revision is a sub-step of scripting.
const STAGES = [
  { status: EXTRACTING_FACTS, agent: "Agent 1", name: "Producer", detail: "Reads the source and debates what must be covered" },
  { status: GENERATING_SCRIPT, agent: "Agents 2 & 3", name: "Hosts", detail: "Draft the two-host dialogue, section by section" },
  { status: SYNTHESIZING_AUDIO, agent: "Agent 4", name: "Audio", detail: "Voices each host and masters the episode" },
] as const;

const STAGE_ORDER: Record<UiStatus, number> = {
  idle: -1,
  [EXTRACTING_FACTS]: 0,
  [GENERATING_SCRIPT]: 1,
  [REVISING_OUTLINE]: 1,
  [SYNTHESIZING_AUDIO]: 2,
  [COMPLETE]: 3,
  [ERROR]: -1,
};

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

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatMinutes(minutes: number): string {
  return formatClock(minutes * 60);
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

/** Deterministic bar heights so the placeholder waveform is stable per episode. */
function waveformBars(seed: string, count: number): number[] {
  let h = 2166136261;
  for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    h = Math.imul(h ^ (h >>> 15), 2246822507) ^ i;
    const noise = ((h >>> 0) % 1000) / 1000;
    const phrase = 0.55 + 0.45 * Math.abs(Math.sin(i / 5.3));
    const envelope = 0.5 + 0.5 * Math.sin((i / count) * Math.PI);
    out.push(Math.max(0.1, envelope * phrase * (0.3 + 0.7 * noise)));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Global styles for the animations (kept in this file on purpose)
// ---------------------------------------------------------------------------

const KEYFRAMES = `
@keyframes pf-shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
@keyframes pf-rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes pf-eq { 0%, 100% { transform: scaleY(0.35); } 50% { transform: scaleY(1); } }
@keyframes pf-glow { 0%, 100% { opacity: 0.45; } 50% { opacity: 0.9; } }
.pf-rise { animation: pf-rise 420ms cubic-bezier(.2,.7,.2,1) both; }
.pf-shimmer::after {
  content: ""; position: absolute; inset: 0;
  background: linear-gradient(100deg, transparent 20%, rgba(255,255,255,0.16) 50%, transparent 80%);
  animation: pf-shimmer 1.8s ease-in-out infinite;
}
.pf-eq > span { transform-origin: bottom; animation: pf-eq 900ms ease-in-out infinite; }
.pf-eq > span:nth-child(2) { animation-delay: -300ms; }
.pf-eq > span:nth-child(3) { animation-delay: -600ms; }
.pf-glow { animation: pf-glow 2.4s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) {
  .pf-rise, .pf-shimmer::after, .pf-eq > span, .pf-glow { animation: none !important; }
}
`;

// ---------------------------------------------------------------------------
// Presentational pieces
// ---------------------------------------------------------------------------

function Equalizer() {
  return (
    <span className="pf-eq inline-flex h-4 items-end gap-[3px]" aria-hidden="true">
      <span className="block h-full w-[3px] rounded-full bg-current" />
      <span className="block h-full w-[3px] rounded-full bg-current" />
      <span className="block h-full w-[3px] rounded-full bg-current" />
    </span>
  );
}

function PipelineRail({ status, message, failedStage }: { status: UiStatus; message: string; failedStage: number }) {
  const current = STAGE_ORDER[status];
  return (
    <ol className="space-y-1">
      {STAGES.map((stage, i) => {
        const done = current > i;
        const active = current === i;
        const failed = status === ERROR && failedStage === i;
        return (
          <li
            key={stage.status}
            className={[
              "relative flex gap-3 rounded-xl px-3 py-3 transition-colors duration-300",
              active ? "bg-violet-500/10 ring-1 ring-violet-400/30" : failed ? "bg-rose-500/10 ring-1 ring-rose-400/30" : "",
            ].join(" ")}
          >
            <div
              className={[
                "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-all duration-300",
                done
                  ? "bg-emerald-400/15 text-emerald-300 ring-1 ring-emerald-400/40"
                  : active
                    ? "bg-violet-500 text-white shadow-[0_0_18px_rgba(139,92,246,0.55)]"
                    : failed
                      ? "bg-rose-500/20 text-rose-200 ring-1 ring-rose-400/40"
                      : "bg-white/[0.04] text-zinc-500 ring-1 ring-white/10",
              ].join(" ")}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : active ? <Equalizer /> : failed ? <X className="h-3.5 w-3.5" /> : i + 1}
            </div>
            <div className="min-w-0">
              <p className={`text-sm font-medium ${active || done ? "text-zinc-100" : "text-zinc-400"}`}>
                {stage.name} <span className="ml-1 text-xs font-normal text-zinc-500">{stage.agent}</span>
              </p>
              <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">{stage.detail}</p>
              {active && message && (
                <p key={message} className="pf-rise mt-1.5 text-xs text-violet-200/90">
                  {message}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function AudioWidget({ result }: { result: CompleteData }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [speed, setSpeed] = useState<number>(1);
  const [loadError, setLoadError] = useState(false);
  const bars = useMemo(() => waveformBars(result.audio_url, 96), [result.audio_url]);
  const src = `${API_BASE}${result.audio_url}`;
  const fraction = duration > 0 ? time / duration : 0;

  const toggle = () => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) void el.play().catch(() => setLoadError(true));
    else el.pause();
  };

  const seekTo = (ratio: number) => {
    const el = audioRef.current;
    if (!el || !duration) return;
    el.currentTime = Math.max(0, Math.min(1, ratio)) * duration;
  };

  const cycleSpeed = () => {
    const next = SPEEDS[(SPEEDS.indexOf(speed as (typeof SPEEDS)[number]) + 1) % SPEEDS.length];
    setSpeed(next);
    if (audioRef.current) audioRef.current.playbackRate = next;
  };

  return (
    <div className="rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.02] p-4 sm:p-5">
      {/* The browser's native element, bound straight to the backend's static mp3 URL. */}
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          setDuration(e.currentTarget.duration);
          e.currentTarget.playbackRate = speed;
        }}
        onError={() => setLoadError(true)}
        className="hidden"
      />

      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={toggle}
          aria-label={playing ? "Pause" : "Play"}
          className="group relative flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 transition hover:scale-105 active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
        >
          {playing && <span className="pf-glow absolute inset-0 rounded-full bg-violet-400/40 blur-md" aria-hidden="true" />}
          {playing ? <Pause className="relative h-6 w-6" fill="currentColor" /> : <Play className="relative ml-0.5 h-6 w-6" fill="currentColor" />}
        </button>

        <div className="min-w-0 flex-1">
          {/* Placeholder waveform; played bars light up, click to seek. */}
          <div
            role="slider"
            tabIndex={0}
            aria-label="Seek"
            aria-valuemin={0}
            aria-valuemax={Math.round(duration)}
            aria-valuenow={Math.round(time)}
            aria-valuetext={`${formatClock(time)} of ${formatClock(duration)}`}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              seekTo((e.clientX - rect.left) / rect.width);
            }}
            onKeyDown={(e) => {
              if (!duration) return;
              if (e.key === "ArrowRight") seekTo((time + 10) / duration);
              if (e.key === "ArrowLeft") seekTo((time - 10) / duration);
              if (e.key === " " || e.key === "Enter") {
                e.preventDefault();
                toggle();
              }
            }}
            className="flex h-16 cursor-pointer items-center gap-[2px] rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/60"
          >
            {bars.map((v, i) => {
              const played = (i + 0.5) / bars.length <= fraction;
              return (
                <span
                  key={i}
                  className={`block flex-1 rounded-full transition-colors duration-150 ${
                    played ? "bg-gradient-to-t from-violet-500 to-fuchsia-400" : "bg-white/15"
                  }`}
                  style={{ height: `${Math.round(v * 100)}%` }}
                />
              );
            })}
          </div>
          <div className="mt-1.5 flex items-center justify-between text-xs tabular-nums text-zinc-400">
            <span>{formatClock(time)}</span>
            <span>{duration ? formatClock(duration) : formatMinutes(result.coverage.estimated_minutes)}</span>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={cycleSpeed}
          className="rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs font-semibold tabular-nums text-zinc-200 transition hover:bg-white/[0.08]"
          aria-label="Playback speed"
        >
          {speed}×
        </button>
        <a
          href={src}
          download
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs font-medium text-zinc-200 transition hover:bg-white/[0.08]"
        >
          <Download className="h-3.5 w-3.5" /> MP3
        </a>
        <a
          href={`${API_BASE}${result.transcript_url}?download=1`}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs font-medium text-zinc-200 transition hover:bg-white/[0.08]"
        >
          <FileText className="h-3.5 w-3.5" /> Transcript
        </a>
        {result.tts_provider === "stub" && (
          <span className="ml-auto rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-1 text-xs font-medium text-amber-200">
            Placeholder audio: silent voices
          </span>
        )}
      </div>
      {loadError && (
        <p className="mt-3 text-xs text-rose-300" role="alert">
          The audio could not be loaded from {src}.
        </p>
      )}
    </div>
  );
}

function TranscriptView({ markdown }: { markdown: string }) {
  const lines = markdown.split("\n");
  return (
    <div className="space-y-2.5 text-sm leading-relaxed text-zinc-300">
      {lines.map((line, i) => {
        if (!line.trim()) return null;
        if (line.startsWith("# ")) return <h3 key={i} className="pt-1 text-lg font-semibold text-white">{line.slice(2)}</h3>;
        if (line.startsWith("## ")) return <h4 key={i} className="pt-4 text-base font-semibold text-violet-200">{line.slice(3)}</h4>;
        if (line === "---") return <hr key={i} className="border-white/10" />;
        const host = line.match(/^(Host [AB](?: \([^)]*\))?):\s(.*)$/);
        if (host) {
          const isA = host[1].startsWith("Host A");
          return (
            <p key={i}>
              <span className={`font-semibold ${isA ? "text-violet-300" : "text-sky-300"}`}>{host[1]}:</span> {host[2]}
            </p>
          );
        }
        if (line.startsWith("- ")) return <p key={i} className="pl-4 text-xs text-zinc-500">• {line.slice(2)}</p>;
        const cleaned = line.replace(/\*\*(.*?)\*\*/g, "$1").replace(/^_(.*)_$/, "$1").replace(/\s{2}$/, "");
        return <p key={i} className="text-zinc-400">{cleaned}</p>;
      })}
    </div>
  );
}

function Stat({ label, value, sub, tone = "neutral" }: { label: string; value: string; sub?: string; tone?: "neutral" | "good" | "warn" }) {
  const color = tone === "good" ? "text-emerald-300" : tone === "warn" ? "text-amber-300" : "text-white";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${color}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-zinc-500">{sub}</p>}
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
  const stageRef = useRef(-1); // last pipeline stage reached, for marking where a run failed
  const [failedStage, setFailedStage] = useState(-1);

  const busy = isInProgress(status === "idle" ? null : status);
  const hasInput = pasteMode ? pastedText.trim().length > 0 : file !== null;

  useEffect(() => () => abortRef.current?.abort(), []);

  // ----------------------------------------------------------- input handling

  const acceptFile = useCallback((candidate: File | undefined | null) => {
    if (!candidate) return;
    const problem = validateFile(candidate);
    if (problem) {
      setFileError(problem);
      setFile(null);
      return;
    }
    setFileError(null);
    setPasteMode(false);
    setFile(candidate);
  }, []);

  const removeFile = () => {
    setFile(null);
    setFileError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // Accepts a dropped file, or dropped plain text (e.g. a selection dragged from another tab).
  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (busy) return;
    const files = e.dataTransfer.files;
    if (files.length > 1) {
      setFileError("Drop one reading at a time.");
      return;
    }
    if (files.length === 1) {
      acceptFile(files[0]);
      return;
    }
    const text = e.dataTransfer.getData("text/plain");
    if (text.trim()) {
      setFileError(null);
      removeFile();
      setPastedText((prev) => (pasteMode && prev.trim() ? `${prev.trimEnd()}\n\n${text}` : text));
      setPasteMode(true);
    }
  };

  const dragHandlers = {
    onDragEnter: (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      if (!busy) setDragActive(true);
    },
    onDragOver: (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = busy ? "none" : "copy";
      if (!busy) setDragActive(true);
    },
    onDragLeave: (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      if (e.currentTarget.contains(e.relatedTarget as Node)) return;
      setDragActive(false);
    },
    onDrop,
  };

  // ----------------------------------------------------------------- stream

  const failWith = (detail: string) => {
    setFailedStage(stageRef.current);
    setStatus(ERROR);
    setErrorDetail(detail);
    setMessage(detail);
    setProgress(100);
  };

  const handleEvent = (event: StreamEvent) => {
    if (event.status === ERROR) setFailedStage(stageRef.current);
    else stageRef.current = STAGE_ORDER[event.status];
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
      setFileError(pasteMode ? "Paste or drop the reading's text first." : "Drop a reading or click to browse first.");
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
    setFailedStage(-1);
    stageRef.current = STAGE_ORDER[EXTRACTING_FACTS];
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
      failWith(`Could not reach the PodcastFactory server at ${API_BASE}. Is the backend running? (${reason})`);
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
      .catch((e: Error) => !cancelled && setTranscriptError(`Could not load the transcript (${e.message}).`));
    return () => {
      cancelled = true;
    };
  }, [result]);

  // ----------------------------------------------------------------- render

  const coverage = result?.coverage;
  const allCovered = coverage ? coverage.must_cover_hit === coverage.must_cover_total : false;
  const durationOff = coverage ? Math.abs(coverage.estimated_minutes - submittedMinutes) / submittedMinutes > 0.1 : false;
  const ButtonIcon = BUTTON_ICONS[status];
  const episodeTitle = transcript?.match(/^# (.+)$/m)?.[1];

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-zinc-950 text-zinc-100">
      <style>{KEYFRAMES}</style>

      {/* Ambient backdrop */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 left-1/2 h-[480px] w-[880px] -translate-x-1/2 rounded-full bg-violet-600/15 blur-[120px]" />
        <div className="absolute right-[-10%] top-[30%] h-[360px] w-[360px] rounded-full bg-fuchsia-600/10 blur-[110px]" />
        <div className="absolute inset-0 bg-[radial-gradient(rgba(255,255,255,0.05)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:linear-gradient(to_bottom,black,transparent_70%)]" />
      </div>

      <main className="relative mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
        {/* Header */}
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-500 shadow-lg shadow-violet-500/25">
              <Headphones className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-white sm:text-2xl">PodcastFactory</h1>
              <p className="text-sm text-zinc-400">Turn an assigned reading into a two-host episode.</p>
            </div>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-400">
            <span className={`h-2 w-2 rounded-full ${busy ? "pf-glow bg-violet-400" : status === ERROR ? "bg-rose-400" : "bg-emerald-400"}`} />
            {busy ? "Pipeline running" : status === ERROR ? "Last run failed" : "Ready"}
          </div>
        </header>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          {/* ---------------------------------------------------------- Compose */}
          <section className="rounded-3xl border border-white/10 bg-zinc-900/60 p-5 shadow-2xl shadow-black/40 backdrop-blur-xl sm:p-7">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-medium text-zinc-200">Reading</h2>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setPasteMode((p) => !p);
                  setFileError(null);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium text-violet-300 transition hover:bg-violet-500/10 disabled:opacity-50"
              >
                {pasteMode ? <FileUp className="h-3.5 w-3.5" /> : <ClipboardPaste className="h-3.5 w-3.5" />}
                {pasteMode ? "Upload a file instead" : "Paste text instead"}
              </button>
            </div>

            {/* Dropzone: files or dragged text */}
            <div
              role={pasteMode ? undefined : "button"}
              tabIndex={pasteMode ? undefined : 0}
              aria-label={pasteMode ? undefined : "Upload a reading"}
              onClick={() => !pasteMode && !busy && !file && fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (!pasteMode && (e.key === "Enter" || e.key === " ") && !busy && !file) {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              {...dragHandlers}
              className={[
                "group relative overflow-hidden rounded-2xl border-2 border-dashed transition-all duration-200",
                dragActive
                  ? "scale-[1.01] border-violet-400 bg-violet-500/10 shadow-[0_0_0_6px_rgba(139,92,246,0.12)]"
                  : file || pasteMode
                    ? "border-white/10 bg-black/20"
                    : "cursor-pointer border-white/15 bg-black/20 hover:border-violet-400/50 hover:bg-violet-500/[0.04]",
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

              {dragActive && (
                <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center bg-zinc-950/95 backdrop-blur-sm">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-500/20 ring-1 ring-violet-400/50">
                    <Upload className="h-6 w-6 animate-bounce text-violet-200" />
                  </div>
                  <p className="mt-3 text-sm font-semibold text-violet-100">Release to add the reading</p>
                  <p className="mt-1 text-xs text-violet-200/70">A file, or text dragged from another window</p>
                </div>
              )}

              {pasteMode ? (
                <textarea
                  value={pastedText}
                  onChange={(e) => setPastedText(e.target.value)}
                  disabled={busy}
                  rows={9}
                  placeholder="Paste or drop the full text of the reading here..."
                  className="block w-full resize-y bg-transparent p-4 text-sm leading-relaxed text-zinc-100 placeholder:text-zinc-500 focus:outline-none disabled:opacity-60"
                />
              ) : file ? (
                <div className="flex min-h-[184px] items-center justify-center p-5">
                  <div className="pf-rise flex w-full max-w-md items-center gap-3 rounded-xl border border-white/10 bg-zinc-900/90 px-4 py-3.5 shadow-lg shadow-black/30">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-violet-500/15 ring-1 ring-violet-400/30">
                      <FileText className="h-5 w-5 text-violet-300" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-zinc-100">{file.name}</p>
                      <p className="text-xs text-zinc-500">
                        {extensionOf(file.name).slice(1).toUpperCase()} · {formatBytes(file.size)}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeFile();
                      }}
                      disabled={busy}
                      className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-white/10 hover:text-white"
                      aria-label="Remove file"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex min-h-[184px] flex-col items-center justify-center px-4 py-8 text-center">
                  <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.04] ring-1 ring-white/10 transition group-hover:bg-violet-500/10 group-hover:ring-violet-400/40">
                    <Upload className="h-5 w-5 text-zinc-400 transition group-hover:text-violet-300" />
                  </div>
                  <p className="text-sm font-medium text-zinc-200">Drag and drop a reading, or click to browse</p>
                  <p className="mt-1 text-xs text-zinc-500">PDF, DOCX, PPTX, TXT, or MD up to {MAX_UPLOAD_MB} MB · or drop selected text</p>
                </div>
              )}
            </div>
            {fileError && (
              <p className="mt-2 flex items-start gap-1.5 text-sm text-rose-300" role="alert">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                {fileError}
              </p>
            )}

            {/* Topic bar */}
            <label className="mt-6 block">
              <span className="mb-2 block text-sm font-medium text-zinc-200">Topic focus</span>
              <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-black/30 px-3 transition focus-within:border-violet-400/60 focus-within:ring-4 focus-within:ring-violet-500/15">
                <Target className="h-4 w-4 shrink-0 text-zinc-500" />
                <input
                  type="text"
                  value={focus}
                  onChange={(e) => setFocus(e.target.value)}
                  disabled={busy}
                  maxLength={300}
                  placeholder="Optional: e.g. implications for pricing strategy"
                  className="w-full bg-transparent py-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none disabled:opacity-60"
                />
                {focus && !busy && (
                  <button type="button" onClick={() => setFocus("")} className="rounded p-1 text-zinc-500 hover:text-zinc-200" aria-label="Clear topic">
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </label>

            {/* Length */}
            <div className="mt-5">
              <span className="mb-2 block text-sm font-medium text-zinc-200">Episode length</span>
              <div className="grid grid-cols-5 gap-1 rounded-xl border border-white/10 bg-black/30 p-1" role="radiogroup" aria-label="Target duration">
                {DURATIONS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    role="radio"
                    aria-checked={minutes === d}
                    disabled={busy}
                    onClick={() => setMinutes(d)}
                    className={`rounded-lg py-2 text-sm font-medium tabular-nums transition disabled:opacity-60 ${
                      minutes === d ? "bg-white/10 text-white shadow-inner ring-1 ring-white/15" : "text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
                    }`}
                  >
                    {d} min
                  </button>
                ))}
              </div>
            </div>

            {/* State-driven action button */}
            <button
              type="button"
              onClick={onAction}
              disabled={busy}
              aria-busy={busy}
              aria-live="polite"
              className={[
                "relative mt-7 flex w-full items-center justify-center gap-2.5 overflow-hidden rounded-2xl px-5 py-4 text-sm font-semibold transition-all duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-900",
                busy
                  ? "pf-shimmer cursor-wait bg-gradient-to-r from-violet-600/70 via-fuchsia-600/70 to-violet-600/70 text-white"
                  : status === ERROR
                    ? "bg-rose-500 text-white shadow-lg shadow-rose-500/25 hover:bg-rose-400"
                    : "bg-gradient-to-r from-violet-500 to-fuchsia-500 text-white shadow-lg shadow-violet-500/30 hover:shadow-violet-500/50 hover:brightness-110 active:scale-[0.99]",
              ].join(" ")}
            >
              {busy && (
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-0 bg-white/10 transition-[width] duration-700 ease-out"
                  style={{ width: `${progress}%` }}
                />
              )}
              <span key={status} className="pf-rise relative flex items-center gap-2.5">
                {busy ? (
                  <span className="relative flex h-4 w-4 items-center justify-center">
                    <Loader2 className="absolute h-4 w-4 animate-spin opacity-60" />
                    <ButtonIcon className="h-2.5 w-2.5" />
                  </span>
                ) : (
                  <ButtonIcon className="h-4 w-4" />
                )}
                {BUTTON_LABELS[status]}
              </span>
            </button>

            {status !== "idle" && (
              <div className="mt-3 flex items-center justify-between gap-3 text-xs text-zinc-500" aria-live="polite">
                <span className="min-w-0 truncate">{message}</span>
                <span className="shrink-0 tabular-nums">{progress}%</span>
              </div>
            )}

            {status === ERROR && errorDetail && (
              <div className="pf-rise mt-5 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4" role="alert">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-rose-100">The podcast could not be produced</p>
                    <p className="mt-1 break-words text-sm text-rose-200/80">{errorDetail}</p>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* --------------------------------------------------------- Pipeline */}
          <aside className="rounded-3xl border border-white/10 bg-zinc-900/60 p-5 shadow-2xl shadow-black/40 backdrop-blur-xl sm:p-6 lg:self-start">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-medium text-zinc-200">Pipeline</h2>
              <span className="text-xs tabular-nums text-zinc-500">{status === "idle" ? "Waiting" : `${progress}%`}</span>
            </div>
            <div className="mb-4 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className={`h-full rounded-full transition-all duration-700 ease-out ${
                  status === ERROR ? "bg-rose-500" : status === COMPLETE ? "bg-emerald-400" : "bg-gradient-to-r from-violet-500 to-fuchsia-400"
                }`}
                style={{ width: `${status === "idle" ? 0 : progress}%` }}
              />
            </div>
            <PipelineRail status={status} message={busy ? message : ""} failedStage={failedStage} />
            {status === REVISING_OUTLINE && (
              <p className="pf-rise mt-3 rounded-lg bg-white/[0.03] px-3 py-2 text-xs text-zinc-400">
                The Producer is trimming or expanding sections to hit {submittedMinutes} minutes.
              </p>
            )}
          </aside>
        </div>

        {/* -------------------------------------------------------- Results */}
        {status === COMPLETE && result && coverage && (
          <section className="pf-rise mt-6 rounded-3xl border border-white/10 bg-zinc-900/60 p-5 shadow-2xl shadow-black/40 backdrop-blur-xl sm:p-7">
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-emerald-300">
                  <CheckCircle2 className="h-4 w-4" /> Episode ready
                </p>
                <h2 className="mt-1 text-lg font-semibold text-white sm:text-xl">{episodeTitle ?? "Your podcast"}</h2>
              </div>
            </div>

            <AudioWidget result={result} />

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <Stat
                label="Length"
                value={formatMinutes(coverage.estimated_minutes)}
                sub={`vs ${submittedMinutes}:00 target${durationOff ? " · outside 10% tolerance" : ""}`}
                tone={durationOff ? "warn" : "neutral"}
              />
              <Stat
                label="Coverage"
                value={`${coverage.must_cover_hit} / ${coverage.must_cover_total}`}
                sub="must-cover ideas addressed"
                tone={allCovered ? "good" : "warn"}
              />
              <Stat label="Script" value={coverage.total_words.toLocaleString()} sub="words" />
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
              <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
                <p className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                  <AlertTriangle className="h-4 w-4 text-amber-300" /> Production notes
                </p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zinc-400">
                  {coverage.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="mt-4 overflow-hidden rounded-xl border border-white/10">
              <button
                type="button"
                onClick={() => setTranscriptOpen((o) => !o)}
                aria-expanded={transcriptOpen}
                className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-zinc-200 transition hover:bg-white/[0.03]"
              >
                <BookOpenText className="h-4 w-4 text-zinc-400" />
                Transcript
                <ChevronDown className={`ml-auto h-4 w-4 text-zinc-500 transition ${transcriptOpen ? "rotate-180" : ""}`} />
              </button>
              {transcriptOpen && (
                <div className="max-h-[520px] overflow-y-auto border-t border-white/10 px-4 py-4">
                  {transcript ? (
                    <TranscriptView markdown={transcript} />
                  ) : transcriptError ? (
                    <p className="text-sm text-rose-300">{transcriptError}</p>
                  ) : (
                    <p className="flex items-center gap-2 text-sm text-zinc-400">
                      <Loader2 className="h-4 w-4 animate-spin" /> Loading transcript...
                    </p>
                  )}
                </div>
              )}
            </div>
          </section>
        )}

        <footer className="mt-10 text-center text-xs text-zinc-600">
          Episodes are AI-generated guides to the reading; check important details against the source.
        </footer>
      </main>
    </div>
  );
}
