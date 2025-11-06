import React, { useEffect, useRef, useState } from "react";
import { getIndividualLists, addItemsToIndividualList, IndividualList } from "../api/individualLists";
import { toast } from "../utils/toast";

type MediaType = "movie" | "show";

export type AddableItem = {
  tmdb_id?: number; // required to add; if missing, button will be disabled
  trakt_id?: number | null;
  media_type: MediaType;
  title: string;
  year?: number;
  overview?: string;
  poster_path?: string;
  genres?: string[] | string | null;
};

export function AddToIndividualList({ item, buttonClassName }: { item: AddableItem; buttonClassName?: string }) {
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<IndividualList[]>([]);
  const [loading, setLoading] = useState(false);
  const [addingListId, setAddingListId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const disabled = !item.tmdb_id;

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("click", handleClickOutside);
    }
    return () => document.removeEventListener("click", handleClickOutside);
  }, [open]);

  async function ensureListsLoaded() {
    if (lists.length === 0 && !loading) {
      setLoading(true);
      try {
        const data = await getIndividualLists(1);
        setLists(data || []);
      } catch (err: any) {
        toast.error("Failed to load your lists. Please try again.");
      } finally {
        setLoading(false);
      }
    }
  }

  function mapItemPayload() {
    const genres = Array.isArray(item.genres)
      ? (item.genres as string[]).join(", ")
      : (item.genres ?? undefined);
    return {
      tmdb_id: item.tmdb_id!,
      trakt_id: item.trakt_id ?? undefined,
      media_type: item.media_type,
      title: item.title,
      year: item.year,
      overview: item.overview,
      poster_path: item.poster_path,
      genres,
    } as any;
  }

  async function onSelectList(list: IndividualList) {
    if (!item.tmdb_id) {
      toast.error("Sorry, this item can't be added (missing TMDB ID).", 5000);
      setOpen(false);
      return;
    }
    setAddingListId(list.id);
    try {
      const payload = [mapItemPayload()];
      const res = await addItemsToIndividualList(list.id, payload, 1);
      const added = Number(res?.added || 0);
      const skipped = Number(res?.skipped || 0);
      if (added >= 1) {
        toast.success(`${item.title} added to ${list.name}`);
      } else if (skipped >= 1) {
        toast.info(`${item.title} is already in ${list.name}`);
      } else {
        toast.warning(`No changes for ${list.name}`);
      }
    } catch (err: any) {
      toast.error(err?.message || "Failed to add to list");
    } finally {
      setAddingListId(null);
      setOpen(false);
    }
  }

  const btnClasses = buttonClassName || "p-1.5 rounded bg-gray-800/80 hover:bg-gray-700 text-white text-xs border border-gray-700";

  return (
    <div className="relative" ref={containerRef}>
      <button
        className={btnClasses + (disabled ? " opacity-50 cursor-not-allowed" : "")}
        onClick={async (e) => {
          e.stopPropagation();
          if (disabled) {
            toast.info("Item missing TMDB ID; cannot add to list.");
            return;
          }
          setOpen((v) => !v);
          await ensureListsLoaded();
        }}
        title={disabled ? "TMDB ID missing" : "Add to Individual List"}
      >
        +
      </button>

      {open && (
        <div className="absolute z-50 right-0 mt-2 w-64 bg-gray-950/95 backdrop-blur-sm border border-gray-700 rounded-lg shadow-xl">
          <div className="px-3 py-2 text-xs text-gray-300 border-b border-gray-800">Add to list</div>
          <div className="max-h-64 overflow-auto">
            {loading ? (
              <div className="px-3 py-2 text-sm text-gray-300">Loading…</div>
            ) : lists.length === 0 ? (
              <div className="px-3 py-2 text-sm text-gray-300">No Individual Lists yet.</div>
            ) : (
              lists.map((lst) => (
                <button
                  key={lst.id}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-gray-800/80 text-gray-100 flex items-center justify-between"
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectList(lst);
                  }}
                  disabled={addingListId === lst.id}
                >
                  <span className="whitespace-normal break-words leading-snug pr-2">{lst.name}</span>
                  {addingListId === lst.id && <span className="text-xs text-gray-400 ml-2">Adding…</span>}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default AddToIndividualList;
