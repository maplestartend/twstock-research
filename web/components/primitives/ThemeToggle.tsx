"use client";

import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";

type Preference = "light" | "system" | "dark";

const OPTIONS: { value: Preference; icon: string; label: string }[] = [
  { value: "light",  icon: "light_mode", label: "淺色" },
  { value: "system", icon: "contrast",   label: "跟隨系統" },
  { value: "dark",   icon: "dark_mode",  label: "深色" },
];

function resolveEffective(pref: Preference): "light" | "dark" {
  if (pref === "light" || pref === "dark") return pref;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyPreference(pref: Preference) {
  const html = document.documentElement;
  html.setAttribute("data-theme", resolveEffective(pref));
  try {
    if (pref === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", pref);
  } catch {}
}

function readPreference(): Preference {
  try {
    const v = localStorage.getItem("theme");
    if (v === "light" || v === "dark") return v;
  } catch {}
  return "system";
}

export function ThemeToggle() {
  const [pref, setPref] = useState<Preference>("system");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setPref(readPreference());

    // 當使用者選「跟隨系統」時，監聽 OS 切換即時同步
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => {
      if (readPreference() === "system") applyPreference("system");
    };
    mq.addEventListener("change", listener);
    return () => mq.removeEventListener("change", listener);
  }, []);

  const pick = (p: Preference) => {
    setPref(p);
    applyPreference(p);
  };

  if (!mounted) {
    return <div className="inline-block w-[108px] h-9" aria-hidden />;
  }

  return (
    <div
      role="radiogroup"
      aria-label="主題切換"
      className="inline-flex items-center gap-0.5 p-0.5 rounded-lg bg-subtle border border-[var(--border-default)]"
    >
      {OPTIONS.map((opt) => {
        const active = pref === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            name={`theme-${opt.value}`}
            role="radio"
            aria-checked={active}
            aria-label={opt.label}
            title={opt.label}
            onClick={() => pick(opt.value)}
            className={cn(
              "inline-flex items-center justify-center w-8 h-8 rounded-md transition-colors",
              active
                ? "bg-surface shadow-sm text-[var(--text-primary)]"
                : "text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] hover:bg-surface/60",
            )}
          >
            <Icon name={opt.icon} size={18} filled={active} />
          </button>
        );
      })}
    </div>
  );
}
