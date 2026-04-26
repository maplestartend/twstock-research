"use client";

import { Icon } from "./Icon";
import { btnSecondary } from "@/lib/formClasses";
import { cn } from "@/lib/utils";

/**
 * 純資料 CSV 下載按鈕。
 *
 * 為什麼接 `headers + rows` 而不是 `[{label, value: fn}]`？
 * Next.js RSC → Client component 邊界不能傳 functions。RSC 端先把資料展平成
 * `string[][]`，這個 component 只負責 Blob/anchor click 即可，型別由 RSC 端的
 * `data.map(...)` 自動推斷。
 */
export type CsvCell = string | number | boolean | null | undefined;

function escapeCell(v: CsvCell): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "Y" : "";
  const s = String(v);
  // RFC 4180：含 , 換行 雙引號 → 用雙引號包，內部雙引號 escape 成兩個
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function buildCsv(headers: string[], rows: CsvCell[][]): string {
  const lines = [headers.map(escapeCell).join(",")];
  for (const r of rows) {
    lines.push(r.map(escapeCell).join(","));
  }
  // BOM + CRLF：Excel 開繁中、避免亂碼與斷行錯亂
  return "﻿" + lines.join("\r\n");
}

function todayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

export function DownloadCsvButton({
  headers,
  rows,
  filename,
  label = "下載 CSV",
  size = "md",
  disabled,
}: {
  headers: string[];
  rows: CsvCell[][];
  /** 不含副檔名；空 → 自動帶日期 */
  filename?: string;
  label?: string;
  size?: "sm" | "md";
  disabled?: boolean;
}) {
  const handleClick = () => {
    if (rows.length === 0) return;
    const csv = buildCsv(headers, rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename || `export_${todayISO()}`}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const isEmpty = rows.length === 0;
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled || isEmpty}
      title={isEmpty ? "無資料可下載" : `下載 ${rows.length} 筆`}
      className={cn(btnSecondary, size === "sm" && "h-8 px-2.5 text-xs")}
    >
      <Icon name="download" size={size === "sm" ? 14 : 16} />
      {label}
    </button>
  );
}
