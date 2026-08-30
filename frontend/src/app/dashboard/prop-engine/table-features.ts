import {
  columnFilteringFeature,
  columnVisibilityFeature,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_includesString,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_text,
  tableFeatures,
} from "@tanstack/react-table";

/**
 * TanStack Table v9 composes features explicitly (a real break from the v8
 * `useReactTable({ getSortedRowModel: getSortedRowModel(), ... })` API most
 * examples still show) -- this object is the "feature set" every table on
 * this page is built from. Defined once at module scope (per TanStack's own
 * guidance) and shared by columns.tsx (which needs its type to parametrize
 * `ColumnDef`) and data-table.tsx (which needs the value for `useTable`).
 */
export const tableFeatureSet = tableFeatures({
  columnFilteringFeature,
  columnVisibilityFeature,
  globalFilteringFeature,
  rowSortingFeature,
  filteredRowModel: createFilteredRowModel(),
  sortedRowModel: createSortedRowModel(),
  filterFns: { includesString: filterFn_includesString },
  sortFns: { alphanumeric: sortFn_alphanumeric, text: sortFn_text },
});

export type TableFeatureSet = typeof tableFeatureSet;
