import type { CSSProperties, HTMLAttributes, ReactNode, Ref, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cx } from "@/lib/cx";
import type { ModuleId } from "./modules";

type TableProps = {
  /** Accessible name; rendered as a visually hidden <caption>. */
  caption: string;
  children: ReactNode;
  /** dense = 44px read-only rows; comfortable = 56px rows for interactive tables. */
  density?: "dense" | "comfortable";
  /** Below 640px each row becomes a stacked block; give every <Td> a `label`. */
  stack?: boolean;
  module?: ModuleId;
  /** e.g. "60vh" or "420px": makes the body scroll with a sticky header. */
  maxHeight?: string;
  /** Extra class on the scroll container (e.g. to let a screen make it fill its column). */
  className?: string;
};

/**
 * Dense table primitives on real <table> semantics: sticky header, tabular numbers, one scroll container.
 * This is a styling layer only. For thousands of rows use server pagination or windowing in the caller.
 */
export function DataTable({ caption, children, density = "comfortable", stack, module, maxHeight, className }: TableProps) {
  const style = maxHeight ? ({ "--table-max-height": maxHeight } as CSSProperties) : undefined;
  return (
    <div className={cx("ui-table-wrap", className)} data-module={module} style={style} tabIndex={0} role="region" aria-label={caption}>
      <table className={cx("ui-table", density === "dense" && "ui-table--dense", stack && "ui-table--stack")}>
        <caption className="ui-sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function Th({ numeric, className, ...rest }: ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return <th scope="col" className={cx("ui-th", numeric && "ui-th--num", className)} {...rest} />;
}

export function Tr({ interactive, selected, className, ...rest }: HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean; selected?: boolean; ref?: Ref<HTMLTableRowElement> }) {
  return <tr data-interactive={interactive || undefined} aria-selected={selected || undefined} className={cx("ui-tr", className)} {...rest} />;
}

export function Td({ numeric, muted, label, className, ...rest }: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean; muted?: boolean; label?: string }) {
  return <td data-label={label} className={cx("ui-td", numeric && "ui-td--num", muted && "ui-td--muted", className)} {...rest} />;
}
