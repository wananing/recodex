import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export type DashboardSelectOption<T extends string = string> = {
  value: T;
  label: string;
  disabled?: boolean;
};

type DashboardSelectProps<T extends string = string> = {
  value: T | "";
  options: DashboardSelectOption<T>[];
  onChange: (value: T) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  contentClassName?: string;
  size?: "sm" | "md" | "card";
};

export function DashboardSelect<T extends string = string>({
  value,
  options,
  onChange,
  placeholder = "Select",
  ariaLabel,
  disabled,
  className,
  triggerClassName,
  contentClassName,
  size = "md",
}: DashboardSelectProps<T>) {
  const selectedValue = options.some((option) => option.value === value) ? value : undefined;

  return (
    <Select value={selectedValue} onValueChange={(nextValue) => onChange(nextValue as T)} disabled={disabled || options.length === 0}>
      <SelectTrigger
        aria-label={ariaLabel ?? placeholder}
        className={cn("dashboard-select-trigger", `dashboard-select-trigger-${size}`, className, triggerClassName)}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className={cn("dashboard-select-content", contentClassName)}>
        {options.map((option) => (
          <SelectItem
            key={option.value}
            value={option.value}
            disabled={option.disabled}
            className="dashboard-select-item"
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

type DashboardSelectCardProps<T extends string = string> = {
  value: T | "";
  options: DashboardSelectOption<T>[];
  onChange: (value: T) => void;
  label: string;
  icon: ReactNode;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
};

export function DashboardSelectCard<T extends string = string>({
  value,
  options,
  onChange,
  label,
  icon,
  placeholder = "Select",
  ariaLabel,
  disabled,
}: DashboardSelectCardProps<T>) {
  const selectedValue = options.some((option) => option.value === value) ? value : undefined;

  return (
    <Select value={selectedValue} onValueChange={(nextValue) => onChange(nextValue as T)} disabled={disabled || options.length === 0}>
      <SelectTrigger
        aria-label={ariaLabel ?? label}
        className="codex-selector-button codex-selector-field codex-card-select-trigger"
      >
        {icon}
        <span className="codex-selector-copy">
          <span>{label}</span>
          <SelectValue placeholder={placeholder} />
        </span>
      </SelectTrigger>
      <SelectContent className="dashboard-select-content">
        {options.map((option) => (
          <SelectItem
            key={option.value}
            value={option.value}
            disabled={option.disabled}
            className="dashboard-select-item"
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
