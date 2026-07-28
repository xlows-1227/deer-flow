"use client";

import { Input } from "@/components/ui/input";
import type { QuotaOverrides } from "@/core/published-agents";

export const quotaFields = [
  "max_concurrent_runs",
  "daily_runs",
  "daily_tokens",
  "max_run_seconds",
  "max_tokens_per_run",
  "max_input_bytes",
  "inbound_rps",
] as const satisfies readonly (keyof QuotaOverrides)[];

export type QuotaField = (typeof quotaFields)[number];
export type QuotaInput = Record<QuotaField, string>;
export type QuotaInputError = "invalid" | "maximum";

export function createEmptyQuotaInput(): QuotaInput {
  return Object.fromEntries(
    quotaFields.map((field) => [field, ""]),
  ) as QuotaInput;
}

export function quotaOverridesToInput(overrides: QuotaOverrides): QuotaInput {
  return Object.fromEntries(
    quotaFields.map((field) => [
      field,
      overrides[field] === undefined ? "" : String(overrides[field]),
    ]),
  ) as QuotaInput;
}

export function parseQuotaInput(
  input: QuotaInput,
  maximums?: Required<QuotaOverrides>,
): {
  overrides: QuotaOverrides;
  errors: Partial<Record<QuotaField, QuotaInputError>>;
} {
  const overrides: QuotaOverrides = {};
  const errors: Partial<Record<QuotaField, QuotaInputError>> = {};
  for (const field of quotaFields) {
    const raw = input[field].trim();
    if (!raw) {
      continue;
    }
    const value = Number(raw);
    if (!Number.isInteger(value) || value <= 0) {
      errors[field] = "invalid";
      continue;
    }
    if (maximums && value > maximums[field]) {
      errors[field] = "maximum";
      continue;
    }
    overrides[field] = value;
  }
  return { overrides, errors };
}

export function QuotaFieldInput({
  id,
  field,
  value,
  label,
  placeholder,
  onChange,
  invalid = false,
  maximum,
}: {
  id: string;
  field: QuotaField;
  value: string;
  label: string;
  placeholder: string;
  onChange: (field: QuotaField, value: string) => void;
  invalid?: boolean;
  maximum?: number;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-xs font-medium">
        {label}
      </label>
      <Input
        id={id}
        type="number"
        min={1}
        max={maximum}
        step={1}
        value={value}
        placeholder={placeholder}
        aria-invalid={invalid}
        onChange={(event) => onChange(field, event.target.value)}
      />
    </div>
  );
}
