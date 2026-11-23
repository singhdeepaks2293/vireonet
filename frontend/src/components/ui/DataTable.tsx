import { flexRender, type ColumnDef, type SortingState, type Table as TanTable } from "@tanstack/react-table";
import type { ReactNode } from "react";

export function DataTable<T>({ table, density, emptyText }: { table: TanTable<T>; density: "comfortable" | "compact"; emptyText: string }) {
  const pad = density === "compact" ? "py-0" : "py-1";
  return (
    <div className="border border-border bg-surface rounded overflow-auto">
      <table>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => (
                <th key={header.id} style={{ width: header.getSize() }} className="relative">
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      className="text-left font-normal"
                      onClick={header.column.getToggleSortingHandler()}
                      aria-label={`Sort by ${String(header.column.id)}`}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {header.column.getIsSorted() === "asc" ? " ↑" : header.column.getIsSorted() === "desc" ? " ↓" : ""}
                    </button>
                    <div
                      role="separator"
                      aria-orientation="vertical"
                      tabIndex={0}
                      onMouseDown={header.getResizeHandler()}
                      onTouchStart={header.getResizeHandler()}
                      onKeyDown={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") e.preventDefault(); }}
                      className="absolute right-0 top-0 h-full w-1 cursor-col-resize touch-none select-none"
                      aria-label={`Resize ${String(header.column.id)} column`}
                    />
                  </div>
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 ? (
            <tr><td colSpan={table.getAllColumns().length} className="text-sm text-text-muted">{emptyText}</td></tr>
          ) : (
            table.getRowModel().rows.map((row) => (
              <tr key={row.id} data-id={(row.original as { id?: string }).id} tabIndex={0} className={`hover:bg-muted focus:bg-muted outline-none ${pad}`}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className={pad}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export type { ColumnDef, SortingState };
export type { ReactNode };
