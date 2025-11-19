import React, { useEffect, useState } from "react";
import { getLists, api } from "../hooks/useApi";
import { formatRelativeTime, formatLocalDate } from "../utils/date";
import { motion, AnimatePresence } from "framer-motion";
import CreateListForm from "./CreateListForm";
import ListCard from "./ListCard";
import ListDetails from "./ListDetails";
import SuggestedLists from "./SuggestedLists";
import AiListManager from "./AiListManager";
import Settings from "./Settings";
import HomePage from "./HomePage";
import { theme } from "../theme";
import IndividualListManager from "./individual/IndividualListManager";
import IndividualListDetail from "./individual/IndividualListDetail";
import ListModal from "./ListModal";
import EditListModal from "./EditListModal";
import { toast } from "../utils/toast";
import PageTransition from "./PageTransition";
import PhaseTimeline from "./phases/PhaseTimeline";
import Overview from "./Overview";
import PairwiseTrainer from "./PairwiseTrainer";

import { StatusWidgets } from "./StatusWidgets";
import { useTraktAccount } from "../hooks/useTraktAccount";

// URL routing utilities
const getViewFromUrl = (): { view: string; listId?: number } => {
  const hash = window.location.hash.slice(1); // Remove #
  if (!hash) return { view: 'home' };
  if (hash === 'lists') return { view: 'lists' };
  if (hash === 'timeline') return { view: 'timeline' };
  if (hash === 'trainer') return { view: 'trainer' };
  
  const [view, id] = hash.split('/');
  if (view === 'list' && id) {
    return { view: 'listDetails', listId: parseInt(id) };
  }
  
  return { view: hash || 'home' };
};

const updateUrl = (view: string, listId?: number) => {
  if (view === 'listDetails' && listId) {
    window.location.hash = `list/${listId}`;
  } else if (view === 'home') {
    window.location.hash = '';
  } else if (view === 'lists') {
    window.location.hash = 'lists';
  } else {
    window.location.hash = view;
  }
};

export default function Dashboard({ onRegisterNavigateHome }: { onRegisterNavigateHome?: (callback: () => void) => void }){
  const { account, loading: accountLoading } = useTraktAccount();
  const [lists, setLists] = useState<any[]>([]);
  const [view, setView] = useState<"home"|"lists"|"create"|"suggested"|"settings"|"listDetails"|"dynamic"|"myLists"|"myListDetails"|"status"|"timeline"|"overview"|"trainer">("home");
  const [selectedList, setSelectedList] = useState<{id:number; title:string}|null>(null);
  const [selectedIndividualListId, setSelectedIndividualListId] = useState<number|null>(null);
  const [editingId, setEditingId] = useState<number|null>(null);
  const [savingId, setSavingId] = useState<number|null>(null);
  const [editValues, setEditValues] = useState<Record<number, any>>({});
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showSuggestedModal, setShowSuggestedModal] = useState(false);
  const [modalListId, setModalListId] = useState<number|null>(null);
  const [modalListTitle, setModalListTitle] = useState<string>("");
  const [editListId, setEditListId] = useState<number|null>(null);
  const [editListTitle, setEditListTitle] = useState<string>("");

  // Dynamic background based on current view
  const getBackgroundGradient = (currentView: string): string => {
    switch (currentView) {
      case 'home':
        return 'bg-gradient-to-br from-indigo-900 via-purple-900 to-fuchsia-900';
      case 'dynamic':
        return 'bg-gradient-to-br from-purple-900 via-fuchsia-900 to-pink-900';
      case 'myLists':
      case 'myListDetails':
        return 'bg-gradient-to-br from-blue-900 via-indigo-900 to-purple-900';
      case 'status':
      case 'settings':
        return 'bg-gradient-to-br from-slate-900 via-gray-900 to-zinc-900';
      case 'timeline':
        return 'bg-gradient-to-br from-violet-900 via-indigo-900 to-blue-900';
      case 'overview':
        return 'bg-gradient-to-br from-purple-900 via-fuchsia-900 to-pink-900';
      case 'trainer':
        return 'bg-gradient-to-br from-indigo-900 via-purple-900 to-violet-900';
      case 'create':
        return 'bg-gradient-to-br from-emerald-900 via-teal-900 to-cyan-900';
      case 'suggested':
        return 'bg-gradient-to-br from-rose-900 via-pink-900 to-fuchsia-900';
      default:
        // lists, listDetails
        return 'bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900';
    }
  };

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
  const changeView = (newView: "home"|"lists"|"create"|"suggested"|"settings"|"listDetails"|"dynamic"|"myLists"|"myListDetails"|"status"|"timeline"|"overview"|"trainer", listId?: number, listTitle?: string) => {
    setView(newView);
    if (newView === 'listDetails' && listId && listTitle) {
      setSelectedList({ id: listId, title: listTitle });
      updateUrl(newView, listId);
    } else if (newView === 'myListDetails' && listId) {
      setSelectedIndividualListId(listId);
    } else {
      setSelectedList(null);
      setSelectedIndividualListId(null);
      updateUrl(newView);
    }
  };

  // Register navigation callback with parent
  useEffect(() => {
    if (onRegisterNavigateHome) {
      onRegisterNavigateHome(() => changeView("home"));
    }
  }, [onRegisterNavigateHome]);

  async function load(){
    try {
      console.log('[Dashboard] Starting to load lists...');
      const data = await getLists();
      console.log('[Dashboard] Fetched lists:', data?.length || 0, 'items', data);
      setLists(data || []);
    } catch(e: any){
      console.error('[Dashboard] Failed to load lists:', e);
      console.error('[Dashboard] Error details:', {
        message: e?.message,
        response: e?.response,
        request: e?.request
      });
      
      // Show user-friendly error toast
      if (e.isRateLimit) {
        toast.error('Trakt rate limit exceeded. Lists will load when available.', 6000);
      } else if (e.isTimeout) {
        toast.warning('Loading lists is taking longer than expected. Please refresh the page.', 5000);
      } else if (e.message && !e.message.includes('Network Error')) {
        // Only show toast for non-network errors (network errors might be temporary)
        toast.error('Failed to load lists. Please refresh the page.');
      }
      
      setLists([]);
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
    <div className="w-full max-w-[1800px] mx-auto px-2 md:px-8">
      <div className={`${getBackgroundGradient(view)} flex flex-col py-4 rounded-3xl shadow-2xl transition-colors duration-700`}>
        <AnimatePresence mode="wait">
          {view === "home" && (
            <PageTransition key="home">
              <div className="px-4">
                <HomePage />
              </div>
            </PageTransition>
          )}

          {view === "lists" && (
            <PageTransition key="lists">
              <div className="space-y-6 px-4">
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
            {/* Poster grid */}
            {lists.length === 0 ? (
              <div className="text-center py-12 text-white/60">
                <p className="text-lg mb-2">No lists yet</p>
                <p className="text-sm">Create a list to get started!</p>
              </div>
            ) : (
              <motion.div 
                initial="hidden"
                animate="visible"
                variants={{
                  hidden: { opacity: 0 },
                  visible: {
                    opacity: 1,
                    transition: {
                      staggerChildren: 0.05
                    }
                  }
                }}
                className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4"
              >
                {lists.map((l, idx) => (
                  <motion.div
                    key={l.id}
                    variants={{
                      hidden: { opacity: 0, y: 20 },
                      visible: { opacity: 1, y: 0 }
                    }}
                  >
                    <ListCard
                      id={l.id}
                      title={l.title}
                      listType={l.list_type}
                      posterPath={l.poster_path}
                      itemLimit={l.item_limit}
                      onOpen={(id, title)=>{ setModalListId(id); setModalListTitle(title); }}
                      onSynced={()=>load()}
                      onEdit={(id)=>{ 
                        const list = lists.find(lst => lst.id === id);
                        if (list) {
                          setEditListId(id);
                          setEditListTitle(list.title);
                        }
                      }}
                      onDelete={()=>load()}
                    />
                  </motion.div>
                ))}
              </motion.div>
            )}
        </div>
            </PageTransition>
        )}

        {view === "listDetails" && selectedList && (
          <PageTransition key="listDetails">
            <ListDetails listId={selectedList.id} title={selectedList.title} onBack={()=>changeView('lists')} />
          </PageTransition>
        )}
        {view === "myLists" && (
          <PageTransition key="myLists">
            <div className="px-4">
              <IndividualListManager onOpenList={(id:number)=>{ setSelectedIndividualListId(id); setView('myListDetails'); }} />
            </div>
          </PageTransition>
        )}
        {view === "myListDetails" && selectedIndividualListId != null && (
          <PageTransition key={`myListDetails-${selectedIndividualListId}`}>
            <div className="px-4">
              <IndividualListDetail listId={selectedIndividualListId} onBack={()=> setView('myLists')} />
            </div>
          </PageTransition>
        )}

        {view === "create" && (
          <PageTransition key="create">
            <CreateListForm onCreated={()=>{ load(); window.dispatchEvent(new Event('lists-updated')); }} />
          </PageTransition>
        )}

        {view === "dynamic" && (
          <PageTransition key="dynamic">
            <AiListManager />
          </PageTransition>
        )}

        {view === "status" && (
          <PageTransition key="status">
            <div className="px-4 space-y-4">
              <div className="mb-1 text-white/90 text-xl font-semibold">System Status</div>
              <StatusWidgets />
              {/* Quota widget moved from sidebar into System Status */}
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
            </div>
          </PageTransition>
        )}

        {view === "suggested" && (
          <PageTransition key="suggested">
            <SuggestedLists onCreate={()=>{ load(); window.dispatchEvent(new Event('lists-updated')); }} />
          </PageTransition>
        )}
        {view === "settings" && (
          <PageTransition key="settings">
            <Settings />
          </PageTransition>
        )}
        
        {view === "timeline" && (
          <PageTransition key="timeline">
            <div className="px-4 space-y-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-2xl md:text-3xl font-bold text-white">Phase Timeline</h2>
                <button
                  onClick={() => changeView("home")}
                  className="px-4 py-2 bg-white/10 hover:bg-white/20 rounded-lg text-white text-sm transition-colors"
                >
                  ← Back to Home
                </button>
              </div>
              <PhaseTimeline />
            </div>
          </PageTransition>
        )}

        {view === "trainer" && (
          <PageTransition key="trainer">
            <div className="px-4">
              <PairwiseTrainer />
            </div>
          </PageTransition>
        )}

        {view === "overview" && (
          <PageTransition key="overview">
            <Overview />
          </PageTransition>
        )}
        </AnimatePresence>
      </div>

      {/* Sidebar removed: main content now uses full width beside the app sidebar */}

      {/* Create List Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-0 md:p-4" onClick={() => setShowCreateModal(false)}>
          <div className="bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 md:rounded-3xl shadow-2xl max-w-4xl w-full h-full md:h-auto md:max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-gradient-to-r from-indigo-900/95 to-purple-900/95 backdrop-blur-sm border-b border-white/20 p-3 md:p-4 flex justify-between items-center md:rounded-t-3xl z-10">
              <h2 className="text-xl md:text-2xl font-bold text-white">Create New List</h2>
              <button
                onClick={() => setShowCreateModal(false)}
                className="text-white/80 hover:text-white text-2xl leading-none px-3 py-1 hover:bg-white/10 rounded-lg transition-all min-w-[44px] min-h-[44px] flex items-center justify-center"
              >
                ×
              </button>
            </div>
            <div className="p-4 md:p-6">
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
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-0 md:p-4" onClick={() => setShowSuggestedModal(false)}>
          <div className="bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 md:rounded-3xl shadow-2xl max-w-4xl w-full h-full md:h-auto md:max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-gradient-to-r from-indigo-900/95 to-purple-900/95 backdrop-blur-sm border-b border-white/20 p-3 md:p-4 flex justify-between items-center md:rounded-t-3xl z-10">
              <h2 className="text-xl md:text-2xl font-bold text-white">Suggested Lists</h2>
              <button
                onClick={() => setShowSuggestedModal(false)}
                className="text-white/80 hover:text-white text-2xl leading-none px-3 py-1 hover:bg-white/10 rounded-lg transition-all min-w-[44px] min-h-[44px] flex items-center justify-center"
              >
                ×
              </button>
            </div>
            <div className="p-4 md:p-6">
              <SuggestedLists onCreate={() => {
                load();
                window.dispatchEvent(new Event('lists-updated'));
                setShowSuggestedModal(false);
              }} />
            </div>
          </div>
        </div>
      )}

      {/* List Details Modal */}
      {modalListId != null && (
        <ListModal listId={modalListId} title={modalListTitle} onClose={()=> setModalListId(null)} />
      )}

      {/* Edit List Modal */}
      {editListId !== null && (
        <EditListModal
          listId={editListId}
          currentTitle={editListTitle}
          onClose={() => { setEditListId(null); setEditListTitle(""); }}
          onSaved={() => load()}
        />
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
