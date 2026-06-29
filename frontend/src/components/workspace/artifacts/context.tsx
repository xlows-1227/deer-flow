import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { env } from "@/env";

export interface ArtifactsContextType {
  artifacts: string[];
  setArtifacts: (artifacts: string[]) => void;

  selectedArtifact: string | null;
  autoSelect: boolean;
  select: (artifact: string, autoSelect?: boolean) => void;
  deselect: () => void;

  open: boolean;
  autoOpen: boolean;
  setOpen: (open: boolean) => void;
}

const ArtifactsContext = createContext<ArtifactsContextType | undefined>(
  undefined,
);

interface ArtifactsProviderProps {
  children: ReactNode;
}

function sameArtifacts(previous: string[], next: string[]): boolean {
  return (
    previous.length === next.length &&
    previous.every((item, index) => item === next[index])
  );
}

export function ArtifactsProvider({ children }: ArtifactsProviderProps) {
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  const [autoSelect, setAutoSelect] = useState(true);
  const [open, setOpen] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const [autoOpen, setAutoOpen] = useState(true);
  const { setOpen: setSidebarOpen } = useSidebar();

  const updateArtifacts = useCallback((nextArtifacts: string[]) => {
    const next = nextArtifacts ?? [];
    setArtifacts((previous) =>
      sameArtifacts(previous, next) ? previous : next,
    );
  }, []);

  const select = useCallback(
    (artifact: string, autoSelect = false) => {
      setSelectedArtifact((current) =>
        current === artifact ? current : artifact,
      );
      if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true") {
        setSidebarOpen(false);
      }
      if (!autoSelect) {
        setAutoSelect(false);
      }
    },
    [setSidebarOpen, setSelectedArtifact, setAutoSelect],
  );

  const deselect = useCallback(() => {
    setSelectedArtifact(null);
    setAutoSelect(true);
    setOpen(false);
  }, []);

  const setArtifactsOpen = useCallback(
    (isOpen: boolean) => {
      if (!isOpen && autoOpen) {
        setAutoOpen(false);
        setAutoSelect(false);
      }
      setOpen(isOpen);
    },
    [autoOpen],
  );

  const value = useMemo<ArtifactsContextType>(
    () => ({
      artifacts,
      setArtifacts: updateArtifacts,

      open,
      autoOpen,
      autoSelect,
      setOpen: setArtifactsOpen,

      selectedArtifact,
      select,
      deselect,
    }),
    [
      artifacts,
      autoOpen,
      autoSelect,
      deselect,
      open,
      select,
      selectedArtifact,
      setArtifactsOpen,
      updateArtifacts,
    ],
  );

  return (
    <ArtifactsContext.Provider value={value}>
      {children}
    </ArtifactsContext.Provider>
  );
}

export function useArtifacts() {
  const context = useContext(ArtifactsContext);
  if (context === undefined) {
    throw new Error("useArtifacts must be used within an ArtifactsProvider");
  }
  return context;
}
