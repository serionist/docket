/**
 * A day page's who field: the combobox over the names /api/web/ledger/who reads (DR-0714), read only while its list
 * is open and kept in memory with the page's other reads, so a write or the poll brings them up to date.
 */
import { useQuery } from "@tanstack/react-query";
import * as React from "react";
import { WhoBox, type WhoBoxProps } from "@/components/who-box";
import { readWho } from "./api";

export const WHO_KEY = ["day", "who"] as const;

export function WhoField(props: Omit<WhoBoxProps, "people" | "loading" | "onOpenChange">) {
  const [looking, setLooking] = React.useState(false);
  const who = useQuery({ queryKey: WHO_KEY, queryFn: readWho, enabled: looking });
  return <WhoBox {...props} people={who.data ?? []} loading={who.isFetching && !who.data} onOpenChange={setLooking} />;
}
