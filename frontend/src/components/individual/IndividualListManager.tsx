import React, { useEffect, useState } from "react";
import { createIndividualList, deleteIndividualList, getIndividualLists, IndividualList } from "../../api/individualLists";
import { useToast } from "../ToastProvider";
import ListCard from "../ListCard";

export default function IndividualListManager({ onOpenList }: { onOpenList: (id: number) => void }) {
  const [lists, setLists] = useState<IndividualList[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const { addToast } = useToast();

  async function load() {
    setLoading(true);
    try {
      const data = await getIndividualLists(1);
      setLists(data);
    } catch (e) {
      console.error(e);
      addToast({ message: "Failed to load lists", type: "error" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function onCreate() {
    if (!name.trim()) {
      addToast({ message: "Please enter a list name", type: "error" });
      return;
    }
    setCreating(true);
    try {
      const newList = await createIndividualList(name.trim(), description.trim() || undefined, false, 1);
      setName("");
      setDescription("");
      addToast({ message: `Created list: ${newList.name}`, type: "success" });
      await load();
    } catch (e) {
      console.error("Failed to create list", e);
      addToast({ message: "Failed to create list", type: "error" });
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="px-2 md:px-4">
      {/* Create panel */}
      <div className="bg-white/10 border border-white/20 rounded-2xl p-4 mb-4">
        <h2 className="text-white font-semibold mb-3">Create Watch List</h2>
        <div className="flex flex-col md:flex-row gap-2">
          <input
            className="flex-1 px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder:text-white/50"
            placeholder="List name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onCreate()}
          />
          <input
            className="flex-1 px-3 py-2 rounded-lg bg-white/10 border border-white/20 text-white placeholder:text-white/50"
            placeholder="Description (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onCreate()}
          />
          <button
            onClick={onCreate}
            disabled={creating || !name.trim()}
            className="px-4 py-2 rounded-lg bg-green-500 hover:bg-green-600 text-white disabled:opacity-50"
          >{creating ? "Creating..." : "Create"}</button>
        </div>
      </div>

      {/* Two-column layout: left poster grid, right sticky panel */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
        <div className="lg:col-span-2">
          {loading ? (
            <div className="text-white/80">Loading lists…</div>
          ) : lists.length === 0 ? (
            <div className="text-white/60 text-center py-8">No watch lists yet. Create one above to get started!</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {lists.map((l) => (
                <ListCard
                  key={l.id}
                  id={l.id}
                  title={l.name}
                  listType={l.trakt_list_id ? 'synced' : 'local'}
                  posterPath={l.poster_path || undefined}
                  itemLimit={l.item_count}
                  onOpen={() => onOpenList(l.id)}
                  onSynced={() => load()}
                  onDelete={async (id) => {
                    try {
                      await deleteIndividualList(id, 1);
                      addToast({ message: "List deleted", type: "success" });
                      await load();
                    } catch (e) {
                      console.error(e);
                      addToast({ message: "Failed to delete list", type: "error" });
                    }
                  }}
                />
              ))}
            </div>
          )}
        </div>
        <aside className="lg:col-span-1 sticky top-4 self-start">
          <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl p-4">
            <h3 className="text-white font-semibold mb-2">Suggestions</h3>
            <p className="text-white/70 text-sm mb-3">Open a list to see personalized suggestions based on your items. We’ll blend FAISS similarity with your taste and recent picks.</p>
            <ul className="text-white/80 text-sm space-y-1 list-disc pl-5">
              <li>Click a poster to open and manage items</li>
              <li>Use the search in detail view to add items</li>
              <li>Refresh your list to regenerate its poster</li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}
