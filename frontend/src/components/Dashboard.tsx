import React, { useEffect, useState } from "react";
import { getLists, api } from "../hooks/useApi";
import { formatRelativeTime, formatLocalDate } from "../utils/date";
import CreateListForm from "./CreateListForm";
import ListDetails from "./ListDetails";
import SuggestedLists from "./SuggestedLists";
import DynamicDashboard from "./DynamicDashboard";
import Settings from "./Settings";

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
      <div className="lg:col-span-2 bg-gradient-to-br from-fuchsia-100 via-indigo-50 to-blue-100 flex flex-col py-8">
        {/* Mobile-optimized navigation */}
        <div className="grid grid-cols-5 gap-2 mb-4 md:flex md:gap-2">
          <button 
            className={`px-2 py-2 md:px-3 md:py-1 rounded shadow text-sm font-medium transition-colors ${
              view === "lists" ? "bg-indigo-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
            }`} 
            onClick={()=>changeView("lists")}
          >
            Lists
          </button>
          <button 
            className={`px-2 py-2 md:px-3 md:py-1 rounded shadow text-sm font-medium transition-colors ${
              view === "create" ? "bg-indigo-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
            }`} 
            onClick={()=>changeView("create")}
          >
            Create
          </button>
          <button 
            className={`px-2 py-2 md:px-3 md:py-1 rounded shadow text-sm font-medium transition-colors ${
              view === "dynamic" ? "bg-indigo-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
            }`} 
            onClick={()=>changeView("dynamic")}
          >
            Dynamic
          </button>
          <button 
            className={`px-2 py-2 md:px-3 md:py-1 rounded shadow text-sm font-medium transition-colors ${
              view === "suggested" ? "bg-indigo-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
            }`} 
            onClick={()=>changeView("suggested")}
          >
            Suggested
          </button>
          <button 
            className={`px-2 py-2 md:px-3 md:py-1 rounded shadow text-sm font-medium transition-colors ${
              view === "settings" ? "bg-indigo-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
            }`} 
            onClick={()=>changeView("settings")}
          >
            Settings
          </button>
        </div>

        {view === "lists" && (
          <div className="space-y-3">
            {lists.map(l => (
            <div key={l.id} className="bg-white p-3 md:p-4 rounded-lg shadow-sm border border-gray-200">
              {/* Mobile-optimized list item layout */}
              <div className="flex flex-col sm:flex-row sm:justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-gray-900 truncate">
                    <button onClick={()=>changeView('listDetails', l.id, l.title)} className="hover:underline">{l.title}</button>
                  </div>
                  <div className="text-sm text-gray-500 mt-1">
                    <span className="inline-block">{l.list_type}</span>
                    <span className="mx-1">•</span>
                    <span className="inline-block">{l.item_limit} items</span>
                  </div>
                  <div className="text-xs text-gray-400 mt-1 flex items-center gap-2">
                    <span>
                      Last updated: {l.last_updated ? formatRelativeTime(l.last_updated) : 'Never'}
                    </span>
                    <button onClick={load} className="text-[11px] px-1 py-0.5 rounded border border-gray-300 bg-white hover:bg-gray-50">↻</button>
                  </div>
                  {l.last_error && (
                    <div className="text-red-600 text-xs mt-1 p-2 bg-red-50 rounded border border-red-200">
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
                          if ((l.list_type === 'smartlist') && vals.discovery !== undefined) payload.discovery = vals.discovery;
                          if ((l.list_type === 'smartlist') && vals.fusion_mode !== undefined) payload.fusion_mode = vals.fusion_mode;
                          if ((l.list_type === 'smartlist') && vals.media_types !== undefined) payload.media_types = vals.media_types;
                          await api.patch(`/lists/${l.id}`, payload);
                          // Immediately run a full sync
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
                {/* Mobile-optimized action buttons */}
                <div className="flex sm:flex-col gap-2 sm:gap-1">
                  {editingId === l.id ? (
                    <button 
                      onClick={()=>{ /* handled inside panel */ }}
                      disabled
                      className="flex-1 sm:flex-none px-3 py-2 bg-gray-300 text-white rounded-md text-sm font-medium"
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
                          discovery: filters.discovery || 'balanced',
                          fusion_mode: !!filters.fusion_mode,
                          media_types: Array.isArray(filters.media_types) ? filters.media_types : ['movies','shows']
                        };
                        setEditValues(prev=>({...prev, [l.id]: initVals}));
                        setEditingId(l.id);
                      }} 
                      className="flex-1 sm:flex-none px-3 py-2 bg-gray-500 hover:bg-gray-600 text-white rounded-md text-sm font-medium transition-colors touch-manipulation"
                    >
                      Edit
                    </button>
                  )}
                  <button 
                    onClick={()=>api.post(`/lists/${l.id}/sync?user_id=1`)} 
                    className="flex-1 sm:flex-none px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-sm font-medium transition-colors touch-manipulation"
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
                    className="flex-1 sm:flex-none px-3 py-2 bg-red-500 hover:bg-red-600 text-white rounded-md text-sm font-medium transition-colors touch-manipulation"
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
          <DynamicDashboard />
        )}

        {view === "suggested" && <SuggestedLists onCreate={()=>{ load(); window.dispatchEvent(new Event('lists-updated')); }} />}
        {view === "settings" && <Settings />}
      </div>

      {/* Mobile-optimized sidebar - stacks on mobile, sidebar on desktop */}
      <aside className="lg:col-span-1 space-y-4">
        <StatusWidgets />
        <div className="bg-white p-3 md:p-4 rounded-lg shadow-sm border border-gray-200">
          <h4 className="font-semibold text-gray-900">Quota</h4>
          {accountLoading ? (
            <p className="text-sm text-gray-600 mt-1">Checking account...</p>
          ) : account ? (
            <>
              <p className={`text-sm mt-1 ${account.vip ? 'text-green-700' : 'text-gray-700'}`}>{account.message}</p>
              {!account.vip && (
                <p className="text-xs text-gray-500 mt-1">Upgrade your Trakt account to VIP for unlimited lists and higher item limits.</p>
              )}
            </>
          ) : (
            <p className="text-sm text-gray-600 mt-1">Unable to determine quota</p>
          )}
        </div>
      </aside>
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
  const isSmart = list.list_type === 'smartlist';
  const maxItems = account?.max_items_per_list ?? 100;
  const update = (k:string, v:any)=> onChange({ ...values, [k]: v });
  return (
    <div className="mt-3 p-3 rounded border bg-gray-50 space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Title</span>
          <input className="text-xs px-2 py-1 bg-white rounded border" value={values.title || ''} onChange={(e)=>update('title', e.target.value)} />
        </label>
        <label className="text-xs text-gray-700 flex items-center gap-2 bg-white px-2 py-1 rounded border">
          <input type="checkbox" checked={!!values.exclude_watched} onChange={(e)=>update('exclude_watched', e.target.checked)} />
          Exclude Watched
        </label>
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Item limit</span>
          <select className="text-xs px-2 py-1 bg-white rounded border" value={values.item_limit}
            onChange={(e)=>update('item_limit', Math.min(Number(e.target.value), maxItems))}>
            {[10,20,50,100,200,500].filter(n=>n<=maxItems).map(n=> (
              <option key={n} value={n}>{n} items</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Sync interval (hours)</span>
          <input type="number" min={1} max={48} className="text-xs px-2 py-1 bg-white rounded border" value={values.sync_interval ?? ''}
            onChange={(e)=>update('sync_interval', e.target.value ? Number(e.target.value) : undefined)} />
        </label>
        <label className="text-xs text-gray-700 flex flex-col gap-1">
          <span>Full sync cadence (days)</span>
          <input type="number" min={1} max={7} className="text-xs px-2 py-1 bg-white rounded border" value={values.full_sync_days}
            onChange={(e)=>update('full_sync_days', Number(e.target.value))} />
        </label>
      </div>
      {isSmart && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className="text-xs text-gray-700 flex flex-col gap-1">
            <span>Discovery</span>
            <select className="text-xs px-2 py-1 bg-white rounded border" value={values.discovery} onChange={(e)=>update('discovery', e.target.value)}>
              <option value="balanced">Balanced</option>
              <option value="obscure">Obscure</option>
              <option value="popular">Popular</option>
              <option value="very_obscure">Very Obscure</option>
            </select>
          </label>
          <label className="text-xs text-gray-700 flex items-center gap-2 bg-white px-2 py-1 rounded border">
            <input type="checkbox" checked={!!values.fusion_mode} onChange={(e)=>update('fusion_mode', e.target.checked)} />
            Fusion mode
          </label>
          <div className="text-xs text-gray-700">
            <div className="mb-1">Media types</div>
            <div className="flex gap-2">
              <button type="button" onClick={()=>{
                const cur = new Set(values.media_types || []);
                if (cur.has('movies')) cur.delete('movies'); else cur.add('movies');
                update('media_types', Array.from(cur));
              }} className={`px-2 py-1 rounded border ${values.media_types?.includes('movies') ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white'}`}>Movies</button>
              <button type="button" onClick={()=>{
                const cur = new Set(values.media_types || []);
                if (cur.has('shows')) cur.delete('shows'); else cur.add('shows');
                update('media_types', Array.from(cur));
              }} className={`px-2 py-1 rounded border ${values.media_types?.includes('shows') ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white'}`}>Shows</button>
            </div>
          </div>
        </div>
      )}
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="px-3 py-1.5 text-xs rounded border bg-white hover:bg-gray-50">Cancel</button>
        <button onClick={()=>onSave(values)} disabled={!!saving} className={`px-3 py-1.5 text-xs rounded ${saving ? 'bg-gray-300 text-white' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}>{saving ? 'Saving…' : 'Save & Full Sync'}</button>
      </div>
    </div>
  );
}
