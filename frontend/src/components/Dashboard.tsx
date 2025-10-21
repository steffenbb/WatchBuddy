import React, { useEffect, useState } from "react";
import { getLists, api } from "../hooks/useApi";
import { formatRelativeTime, formatLocalDate } from "../utils/date";
import CreateListForm from "./CreateListForm";
import ListDetails from "./ListDetails";
import SuggestedLists from "./SuggestedLists";
import AiListManager from "./AiListManager";
import Settings from "./Settings";
import { theme } from "../theme";

import { StatusWidgets } from "./StatusWidgets";
import { useTraktAccount } from "../hooks/useTraktAccount";

// URL routing utilities
const getViewFromUrl = (): { view: string; listId?: number } => {
  const hash = window.location.hash.slice(1); // Remove #
  if (!hash) return { view: 'lists' };
  
  const [view, id] = hash.split('/');
  if (view === 'list' && id) {
    return { view: 'listDetails', listId: parseInt(id) };
  }
  
  return { view: hash || 'lists' };
};

const updateUrl = (view: string, listId?: number) => {
  if (view === 'listDetails' && listId) {
    window.location.hash = `list/${listId}`;
  } else if (view === 'lists') {
    window.location.hash = '';
  } else {
    window.location.hash = view;
  }
};

export default function Dashboard({ onRegisterNavigateHome }: { onRegisterNavigateHome?: (callback: () => void) => void }){
  const { account, loading: accountLoading } = useTraktAccount();
  const [lists, setLists] = useState<any[]>([]);
  const [view, setView] = useState<"lists"|"create"|"suggested"|"settings"|"listDetails"|"dynamic">("lists");
  const [selectedList, setSelectedList] = useState<{id:number; title:string}|null>(null);
  const [editingId, setEditingId] = useState<number|null>(null);
  const [savingId, setSavingId] = useState<number|null>(null);
  const [editValues, setEditValues] = useState<Record<number, any>>({});
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showSuggestedModal, setShowSuggestedModal] = useState(false);

  // Initialize from URL on load
  useEffect(() => {
    const { view: urlView, listId } = getViewFromUrl();
    setView(urlView as any);
    
    if (urlView === 'listDetails' && listId) {
      // Find the list to set selectedList
      const foundList = lists.find(l => l.id === listId);
      if (foundList) {
        setSelectedList({ id: foundList.id, title: foundList.title });
      }
    }
  }, [lists]);

  // Listen for browser back/forward
  useEffect(() => {
    const handlePopState = () => {
      const { view: urlView, listId } = getViewFromUrl();
      setView(urlView as any);
      
      if (urlView === 'listDetails' && listId) {
        const foundList = lists.find(l => l.id === listId);
        if (foundList) {
          setSelectedList({ id: foundList.id, title: foundList.title });
        }
      } else {
        setSelectedList(null);
      }
    };
    
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [lists]);

  // Helper function to change view and update URL
  const changeView = (newView: "lists"|"create"|"suggested"|"settings"|"listDetails"|"dynamic", listId?: number, listTitle?: string) => {
    setView(newView);
    if (newView === 'listDetails' && listId && listTitle) {
      setSelectedList({ id: listId, title: listTitle });
      updateUrl(newView, listId);
    } else {
      setSelectedList(null);
      updateUrl(newView);
    }
  };

  // Register navigation callback with parent
  useEffect(() => {
    if (onRegisterNavigateHome) {
      onRegisterNavigateHome(() => changeView("lists"));
    }
  }, [onRegisterNavigateHome]);

  async function load(){
    try {
      const data = await getLists();
      setLists(data);
    } catch(e){
      console.error(e);
    }
  }

  useEffect(()=>{ 
    load(); 
    
    // Listen for list updates
    const handleListsUpdated = () => load();
    window.addEventListener('lists-updated', handleListsUpdated);
    
    return () => window.removeEventListener('lists-updated', handleListsUpdated);
  }, []);

  return (
    <div className="w-full max-w-[1800px] mx-auto grid grid-cols-1 lg:grid-cols-3 gap-8 px-2 md:px-8">
      <div className="lg:col-span-2 bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 flex flex-col py-8 rounded-3xl shadow-2xl">
        {/* Modern glassmorphic navigation */}
        <div className="flex flex-wrap gap-2 mb-6 px-4 md:gap-3">
          <button 
            className={`flex-1 min-w-[80px] px-3 md:px-4 py-3 rounded-xl shadow-lg text-xs md:text-sm font-semibold transition-all duration-200 min-h-[44px] ${
              view === "lists" 
                ? "bg-white text-indigo-900 shadow-xl scale-105" 
                : "bg-white/10 backdrop-blur-lg border border-white/20 text-white hover:bg-white/15"
            }`} 
            onClick={()=>changeView("lists")}
          >
            Lists
          </button>
          <button 
            className={`flex-1 min-w-[90px] px-3 md:px-4 py-3 rounded-xl shadow-lg text-xs md:text-sm font-semibold transition-all duration-200 min-h-[44px] flex items-center justify-center gap-1 ${
              view === "dynamic" 
                ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-xl scale-105" 
                : "bg-white/10 backdrop-blur-lg border border-white/20 text-white hover:bg-white/15"
            }`} 
            onClick={()=>changeView("dynamic")}
          >
            <span className="text-lg hidden sm:inline">✨</span>
            <span className="hidden sm:inline">AI Lists</span>
            <span className="sm:hidden">AI</span>
          </button>
          <button 
            className={`flex-1 min-w-[80px] px-3 md:px-4 py-3 rounded-xl shadow-lg text-xs md:text-sm font-semibold transition-all duration-200 min-h-[44px] ${
              view === "settings" 
                ? "bg-white text-indigo-900 shadow-xl scale-105" 
                : "bg-white/10 backdrop-blur-lg border border-white/20 text-white hover:bg-white/15"
            }`} 
            onClick={()=>changeView("settings")}
          >
            <span className="hidden sm:inline">Settings</span>
            <span className="sm:hidden">⚙️</span>
          </button>
        </div>

        {view === "lists" && (
          <div className="space-y-4 px-4">
            {/* Action buttons at top of lists */}
            <div className="flex gap-3 mb-4">
              <button
                onClick={() => setShowCreateModal(true)}
                className="flex-1 px-4 py-3 bg-gradient-to-r from-green-500 to-emerald-500 hover:from-green-600 hover:to-emerald-600 text-white rounded-xl text-sm font-semibold transition-all min-h-[44px] shadow-lg"
              >
                ➕ Create List
              </button>
              <button
                onClick={() => setShowSuggestedModal(true)}
                className="flex-1 px-4 py-3 bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white rounded-xl text-sm font-semibold transition-all min-h-[44px] shadow-lg"
              >
                💡 Suggested Lists
              </button>
            </div>

            {lists.map(l => (
            <div key={l.id} className="bg-white/10 backdrop-blur-lg border border-white/20 p-4 md:p-6 rounded-2xl shadow-lg hover:bg-white/15 transition-all duration-200">
              {/* Modern list item layout */}
              <div className="flex flex-col sm:flex-row sm:justify-between gap-4">
                <div className="flex-1 min-w-0 overflow-hidden">
                  <div className="font-bold text-xl text-white truncate overflow-hidden text-ellipsis whitespace-nowrap">
                    <button onClick={()=>changeView('listDetails', l.id, l.title)} className="hover:text-pink-300 transition-colors truncate overflow-hidden text-ellipsis max-w-full inline-block align-bottom">{l.title}</button>
                  </div>
                  <div className="text-sm text-white/80 mt-2 flex items-center gap-3">
                    <span className="px-3 py-1 bg-purple-500/30 rounded-full">{l.list_type}</span>
                    <span className="px-3 py-1 bg-indigo-500/30 rounded-full">{l.item_limit} items</span>
                  </div>
                  <div className="text-xs text-white/60 mt-2 flex items-center gap-2">
                    <span>
                      Last updated: {l.last_updated ? formatRelativeTime(l.last_updated) : 'Never'}
                    </span>
                    <button onClick={load} className="text-xs px-2 py-1 rounded-lg border border-white/30 bg-white/10 hover:bg-white/20 transition-colors">↻</button>
                  </div>
                  {l.last_error && (
                    <div className="text-red-300 text-xs mt-2 p-3 bg-red-500/20 rounded-lg border border-red-400/30">
                      Error: {l.last_error}
                    </div>
                  )}
                  {/* Edit Panel */}
                  {editingId === l.id && (
                    <EditPanel
                      list={l}
                      account={account}
                      values={editValues[l.id]}
                      onChange={(vals)=> setEditValues(prev=>({...prev, [l.id]: vals}))}
                      onCancel={()=>{ setEditingId(null); setEditValues(prev=>{ const { [l.id]:_, ...rest } = prev; return rest; }); }}
                      onSave={async (vals)=>{
                        setSavingId(l.id);
                        try{
                          const payload: any = {};
                          if (vals.title !== undefined) payload.title = vals.title;
                          if (vals.exclude_watched !== undefined) payload.exclude_watched = vals.exclude_watched;
                          if (vals.item_limit !== undefined) payload.item_limit = vals.item_limit;
                          if (vals.sync_interval !== undefined) payload.sync_interval = vals.sync_interval;
                          if (vals.full_sync_days !== undefined) payload.full_sync_days = vals.full_sync_days;
                          
                          // Custom/Suggested list filters
                          if (vals.genres !== undefined) payload.genres = vals.genres;
                          if (vals.genre_mode !== undefined) payload.genre_mode = vals.genre_mode;
                          if (vals.languages !== undefined) payload.languages = vals.languages;
                          if (vals.year_from !== undefined) payload.year_from = vals.year_from;
                          if (vals.year_to !== undefined) payload.year_to = vals.year_to;
                          if (vals.min_rating !== undefined) payload.min_rating = vals.min_rating;
                          
                          await api.patch(`/lists/${l.id}`, payload);
                          // Immediately run a full sync to apply filter changes
                          await api.post(`/lists/${l.id}/sync?user_id=1&force_full=true`);
                          await load();
                          window.dispatchEvent(new Event('lists-updated'));
                          setEditingId(null);
                        } catch(e){
                          console.error('Failed to save list edits', e);
                        } finally {
                          setSavingId(null);
                        }
                      }}
                      saving={savingId === l.id}
                    />
                  )}
                </div>
                {/* Touch-optimized action buttons */}
                <div className="flex sm:flex-col gap-2">
                  {editingId === l.id ? (
                    <button 
                      onClick={()=>{ /* handled inside panel */ }}
                      disabled
                      className="flex-1 sm:flex-none px-4 py-3 bg-white/20 text-white/50 rounded-xl text-sm font-semibold min-h-[44px]"
                    >Editing…</button>
                  ) : (
                    <button 
                      onClick={()=>{
                        // Initialize edit values from current list
                        let filters: any = {};
                        try { filters = l.filters ? JSON.parse(l.filters) : {}; } catch { filters = {}; }
                        const initVals = {
                          title: l.title,
                          exclude_watched: !!l.exclude_watched,
                          item_limit: l.item_limit || 20,
                          sync_interval: l.sync_interval || undefined,
                          full_sync_days: (filters.full_sync_days || 1),
                          // Custom/Suggested list filters
                          genres: Array.isArray(filters.genres) ? filters.genres : [],
                          genre_mode: filters.genre_mode || 'any',
                          languages: Array.isArray(filters.languages) ? filters.languages : [],
                          year_from: filters.year_from || 2000,
                          year_to: filters.year_to || new Date().getFullYear(),
                          min_rating: filters.min_rating || 0
                        };
                        setEditValues(prev=>({...prev, [l.id]: initVals}));
                        setEditingId(l.id);
                      }} 
                      className="flex-1 sm:flex-none px-4 py-3 bg-white/10 hover:bg-white/20 text-white rounded-xl text-sm font-semibold transition-all min-h-[44px] border border-white/20"
                    >
                      Edit
                    </button>
                  )}
                  <button 
                    onClick={()=>api.post(`/lists/${l.id}/sync?user_id=1`)} 
                    className="flex-1 sm:flex-none px-4 py-3 bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white rounded-xl text-sm font-semibold transition-all min-h-[44px] shadow-lg"
                  >
                    Sync
                  </button>
                  <button 
                    onClick={async()=>{ 
                      if (window.confirm('Delete this list?')) {
                        await api.delete(`/lists/${l.id}?user_id=1`); 
                        load();
                      }
                    }} 
                    className="flex-1 sm:flex-none px-4 py-3 bg-red-500/80 hover:bg-red-600 text-white rounded-xl text-sm font-semibold transition-all min-h-[44px]"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
        )}

        {view === "listDetails" && selectedList && (
          <ListDetails listId={selectedList.id} title={selectedList.title} onBack={()=>changeView('lists')} />
        )}

        {view === "create" && (
          <CreateListForm onCreated={()=>{ load(); window.dispatchEvent(new Event('lists-updated')); }} />
        )}

        {view === "dynamic" && (
          <AiListManager />
        )}

        {view === "suggested" && <SuggestedLists onCreate={()=>{ load(); window.dispatchEvent(new Event('lists-updated')); }} />}
        {view === "settings" && <Settings />}
      </div>

      {/* Mobile-optimized sidebar - stacks on mobile, sidebar on desktop */}
      <aside className="lg:col-span-1 space-y-4">
        <StatusWidgets />
        <div className="bg-white/10 backdrop-blur-lg border border-white/20 p-4 md:p-5 rounded-2xl shadow-lg">
          <h4 className="font-semibold text-white text-lg mb-3">Quota</h4>
          {accountLoading ? (
            <p className="text-sm text-white/60">Checking account...</p>
          ) : account ? (
            <>
              <p className={`text-sm ${account.vip ? 'text-emerald-300' : 'text-white/80'}`}>{account.message}</p>
              {!account.vip && (
                <p className="text-xs text-white/50 mt-2">Upgrade your Trakt account to VIP for unlimited lists and higher item limits.</p>
              )}
            </>
          ) : (
            <p className="text-sm text-gray-600 mt-1">Unable to determine quota</p>
          )}
        </div>
      </aside>

      {/* Create List Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => setShowCreateModal(false)}>
          <div className="bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 rounded-3xl shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-gradient-to-r from-indigo-900/95 to-purple-900/95 backdrop-blur-sm border-b border-white/20 p-4 flex justify-between items-center rounded-t-3xl z-10">
              <h2 className="text-2xl font-bold text-white">Create New List</h2>
              <button
                onClick={() => setShowCreateModal(false)}
                className="text-white/80 hover:text-white text-2xl leading-none px-3 py-1 hover:bg-white/10 rounded-lg transition-all"
              >
                ×
              </button>
            </div>
            <div className="p-6">
              <CreateListForm onCreated={() => {
                load();
                window.dispatchEvent(new Event('lists-updated'));
                setShowCreateModal(false);
              }} />
            </div>
          </div>
        </div>
      )}

      {/* Suggested Lists Modal */}
      {showSuggestedModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => setShowSuggestedModal(false)}>
          <div className="bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 rounded-3xl shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-gradient-to-r from-indigo-900/95 to-purple-900/95 backdrop-blur-sm border-b border-white/20 p-4 flex justify-between items-center rounded-t-3xl z-10">
              <h2 className="text-2xl font-bold text-white">Suggested Lists</h2>
              <button
                onClick={() => setShowSuggestedModal(false)}
                className="text-white/80 hover:text-white text-2xl leading-none px-3 py-1 hover:bg-white/10 rounded-lg transition-all"
              >
                ×
              </button>
            </div>
            <div className="p-6">
              <SuggestedLists onCreate={() => {
                load();
                window.dispatchEvent(new Event('lists-updated'));
                setShowSuggestedModal(false);
              }} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EditPanel({ list, account, values, onChange, onCancel, onSave, saving }:{
  list: any;
  account: any;
  values: any;
  onChange: (v:any)=>void;
  onCancel: ()=>void;
  onSave: (v:any)=>void;
  saving: boolean;
}){
  if (!values) return null;
  const maxItems = account?.max_items_per_list ?? 100;
  const update = (k:string, v:any)=> onChange({ ...values, [k]: v });
  return (
    <div className="mt-4 p-5 rounded-2xl border border-white/30 bg-white/5 backdrop-blur-sm space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Title</span>
          <input 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
            value={values.title || ''} 
            onChange={(e)=>update('title', e.target.value)} 
          />
        </label>
        <label className="text-sm text-white/90 flex items-center gap-3 bg-white/10 backdrop-blur-sm px-4 py-3 rounded-xl border border-white/20 min-h-[44px]">
          <input 
            type="checkbox" 
            checked={!!values.exclude_watched} 
            onChange={(e)=>update('exclude_watched', e.target.checked)} 
            className="w-5 h-5 rounded border-white/30 text-purple-500 focus:ring-purple-400 focus:ring-offset-0"
          />
          <span className="font-medium">Exclude Watched</span>
        </label>
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Item limit</span>
          <select 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
            value={values.item_limit}
            onChange={(e)=>update('item_limit', Math.min(Number(e.target.value), maxItems))}
          >
            {[10,20,50,100,200,500].filter(n=>n<=maxItems).map(n=> (
              <option key={n} value={n}>{n} items</option>
            ))}
          </select>
        </label>
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Sync interval (hours)</span>
          <input 
            type="number" 
            min={1} 
            max={48} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
            value={values.sync_interval ?? ''}
            onChange={(e)=>update('sync_interval', e.target.value ? Number(e.target.value) : undefined)} 
          />
        </label>
        <label className="text-sm text-white/90 flex flex-col gap-2">
          <span className="font-medium">Full sync cadence (days)</span>
          <input 
            type="number" 
            min={1} 
            max={7} 
            className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
            value={values.full_sync_days}
            onChange={(e)=>update('full_sync_days', Number(e.target.value))} 
          />
        </label>
      </div>
      <div className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="text-sm text-white/90 flex flex-col gap-2">
            <span className="font-medium">Year From</span>
            <input 
                type="number" 
                min={1900} 
                max={new Date().getFullYear()} 
                className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
                value={values.year_from || 2000}
                onChange={(e)=>update('year_from', Number(e.target.value))} 
              />
            </label>
            <label className="text-sm text-white/90 flex flex-col gap-2">
              <span className="font-medium">Year To</span>
              <input 
                type="number" 
                min={1900} 
                max={new Date().getFullYear()} 
                className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
                value={values.year_to || new Date().getFullYear()}
                onChange={(e)=>update('year_to', Number(e.target.value))} 
              />
            </label>
            <label className="text-sm text-white/90 flex flex-col gap-2">
              <span className="font-medium">Minimum Rating (0-10)</span>
              <input 
                type="number" 
                min={0} 
                max={10} 
                step={0.1}
                className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white placeholder-white/40 rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
                value={values.min_rating || 0}
                onChange={(e)=>update('min_rating', Number(e.target.value))} 
              />
            </label>
            <label className="text-sm text-white/90 flex flex-col gap-2">
              <span className="font-medium">Genre Mode</span>
              <select 
                className="px-4 py-3 bg-white/10 backdrop-blur-sm text-white rounded-xl border border-white/20 focus:outline-none focus:ring-2 focus:ring-purple-400 transition-all min-h-[44px]" 
                value={values.genre_mode || 'any'} 
                onChange={(e)=>update('genre_mode', e.target.value)}
              >
                <option value="any">Any Genre (OR)</option>
                <option value="all">All Genres (AND)</option>
              </select>
            </label>
          </div>
          <div className="text-sm text-white/90">
            <div className="mb-2 font-medium">Genres (select multiple)</div>
            <div className="flex flex-wrap gap-2">
              {['action','comedy','drama','sci-fi','romance','mystery','thriller','horror','documentary','animation','fantasy','adventure'].map(g => (
                <button
                  key={g}
                  type="button"
                  onClick={()=>{
                    const cur = new Set(values.genres || []);
                    if (cur.has(g)) cur.delete(g); else cur.add(g);
                    update('genres', Array.from(cur));
                  }}
                  className={`px-3 py-2 rounded-lg border transition-all text-sm ${
                    values.genres?.includes(g)
                      ? 'bg-indigo-500 text-white border-indigo-500'
                      : 'bg-white/10 text-white border-white/20 hover:bg-white/15'
                  }`}
                >
                  {g}
                </button>
              ))}
            </div>
          </div>
          <div className="text-sm text-white/90">
            <div className="mb-2 font-medium">Languages (select multiple)</div>
            <div className="flex flex-wrap gap-2">
              {['en','da','sv','no','es','fr','de','it','ja','ko','zh'].map(lang => (
                <button
                  key={lang}
                  type="button"
                  onClick={()=>{
                    const cur = new Set(values.languages || []);
                    if (cur.has(lang)) cur.delete(lang); else cur.add(lang);
                    update('languages', Array.from(cur));
                  }}
                  className={`px-3 py-2 rounded-lg border transition-all text-sm uppercase ${
                    values.languages?.includes(lang)
                      ? 'bg-purple-500 text-white border-purple-500'
                      : 'bg-white/10 text-white border-white/20 hover:bg-white/15'
                  }`}
                >
                  {lang}
                </button>
              ))}
            </div>
          </div>
        </div>
      <div className="flex gap-3 justify-end pt-2">
        <button 
          onClick={onCancel} 
          className="px-6 py-3 rounded-xl border border-white/20 bg-white/10 backdrop-blur-sm text-white hover:bg-white/15 transition-all min-h-[44px] font-medium"
        >
          Cancel
        </button>
        <button 
          onClick={()=>onSave(values)} 
          disabled={!!saving} 
          className={`px-6 py-3 rounded-xl transition-all min-h-[44px] font-medium ${
            saving 
              ? 'bg-white/5 text-white/40 cursor-not-allowed' 
              : 'bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white shadow-lg'
          }`}
        >
          {saving ? 'Saving…' : 'Save & Full Sync'}
        </button>
      </div>
    </div>
  );
}
