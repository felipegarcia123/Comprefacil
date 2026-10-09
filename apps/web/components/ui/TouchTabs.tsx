"use client";

import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Toldo } from "./Toldo";
import type { ModuleId } from "./modules";

export type TouchTabItem = {
  id: string;
  label: string;
  /** Second line, e.g. the sale total or the supplier name. */
  sub?: string;
  icon?: LucideIcon;
  module?: ModuleId;
};

type Props = {
  items: TouchTabItem[];
  value: string;
  onChange: (id: string) => void;
  ariaLabel: string;
  /** Extra non-tab action rendered at the end (e.g. a "+ Nueva" button). */
  trailing?: ReactNode;
};

/** Large, scrollable tabs. Roving tabindex + arrow keys; selected state uses border, weight and toldo (not only color). */
export function TouchTabs({ items, value, onChange, ariaLabel, trailing }: Props) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    refs.current[value]?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [value]);

  function onKeyDown(event: KeyboardEvent) {
    const index = items.findIndex(item => item.id === value);
    let next = -1;
    if (event.key === "ArrowRight") next = (index + 1) % items.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    if (next < 0) return;
    event.preventDefault();
    onChange(items[next].id);
    refs.current[items[next].id]?.focus();
  }

  return (
    <div className="ui-tabs" role="tablist" aria-label={ariaLabel} onKeyDown={onKeyDown}>
      {items.map(item => {
        const selected = item.id === value;
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            ref={element => { refs.current[item.id] = element; }}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            data-module={item.module}
            className="ui-tab"
            onClick={() => onChange(item.id)}
          >
            <Toldo module={item.module} />
            <span className="ui-tab__body">
              {Icon && <Icon size={24} aria-hidden="true" />}
              <span className="ui-tab__text">
                <span>{item.label}</span>
                {item.sub && <span className="ui-tab__sub ui-num">{item.sub}</span>}
              </span>
            </span>
          </button>
        );
      })}
      {trailing}
    </div>
  );
}
