import { useQuery } from "@tanstack/react-query";

import { loadConnectors, loadConnectorTypes } from "./api";

export function useConnectors() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["connectors"],
    queryFn: () => loadConnectors(),
  });
  return { connectors: data ?? [], isLoading, error };
}

export function useConnectorTypes() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["connector-types"],
    queryFn: () => loadConnectorTypes(),
  });
  return { connectorTypes: data ?? [], isLoading, error };
}
