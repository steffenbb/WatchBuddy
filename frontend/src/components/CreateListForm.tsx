import React, { useEffect, useMemo, useState } from "react";
import { api } from "../hooks/useApi";
import { useTraktAccount } from "../hooks/useTraktAccount";


// Dynamic genres/languages from backend
const DEFAULT_GENRES = ["action","comedy","drama","sci-fi","romance","mystery","thriller","documentary"];
const DEFAULT_LANGUAGES = ["en","da","es","fr","de","it","ja","ko","zh"];

export default function CreateListForm({ onCreated }: { onCreated?: ()=>void }){
  const [availableGenres, setAvailableGenres] = useState<string[]>(DEFAULT_GENRES);
  const [availableLanguages, setAvailableLanguages] = useState<string[]>(DEFAULT_LANGUAGES);
  const { account, loading: accountLoading } = useTraktAccount();
  const [listCount, setListCount] = useState<number>(0);

  const [title, setTitle] = useState<string>("");
  const [genres, setGenres] = useState<string[]>(DEFAULT_GENRES.slice());
  const [itemLimit, setItemLimit] = useState<number>(30);
  const [genreMode, setGenreMode] = useState<'any'|'all'>('any');
  const [obscurity, setObscurity] = useState<string[]>([]); // ["very_obscure", "less_obscure", "neutral", "popular"]
  const [languages, setLanguages] = useState<string[]>([]); // e.g. ["en", "da"]
  const [yearFrom, setYearFrom] = useState<number>(2000);
  const [yearTo, setYearTo] = useState<number>(new Date().getFullYear());
  const [watchedStatus, setWatchedStatus] = useState<string>("exclude_watched"); // "exclude_watched", "include_all", "not_watched_recently"
  const [notWatchedDays, setNotWatchedDays] = useState<number>(90);
  const [mediaType, setMediaType] = useState<string[]>(["movies", "shows"]);

  const [message, setMessage] = useState<string>("");
  const [messageType, setMessageType] = useState<"success"|"error"|"">("");

  // Inline validation + submit state
  const [errorTitle, setErrorTitle] = useState<string>("");
  const [errorGenres, setErrorGenres] = useState<string>("");
  const [errorMediaType, setErrorMediaType] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  function toggleGenre(g: string){
    setGenres((prev: string[]) => {
      const next = prev.includes(g) ? prev.filter((x: string)=>x!==g) : [...prev,g];
      // validate
      setErrorGenres(next.length === 0 ? "Select at least one genre" : "");
      return next;
    });
  }
  function toggleObscurity(o: string){
    setObscurity((prev: string[]) => prev.includes(o) ? prev.filter((x: string)=>x!==o) : [...prev,o]);
  }
  function toggleLanguage(l: string){
    setLanguages((prev: string[]) => prev.includes(l) ? prev.filter((x: string)=>x!==l) : [...prev,l]);
  }
  function toggleMediaType(t: string){
    setMediaType((prev: string[]) => {
      const next = prev.includes(t) ? prev.filter((x: string)=>x!==t) : [...prev,t];
      setErrorMediaType(next.length === 0 ? "Select at least one type (movie/show)" : "");
      return next;
    });
  }

  const isFormValid = title.trim().length > 0 && genres.length > 0 && mediaType.length > 0;
  const maxItems = useMemo(()=> (account?.max_items_per_list ?? 100), [account]);
  const canCreateMoreLists = useMemo(()=>{
    if (!account) return false; // wait until loaded
    if (account.vip) return true;
    if (account.max_lists === null || account.max_lists === undefined) return true;
    return listCount < account.max_lists;
  }, [account, listCount]);

  async function refreshListCount(){
    try{
      const res = await api.get('/lists/');
      const data = Array.isArray(res.data) ? res.data : (res.data?.lists || []);
      setListCount(data.length || 0);
    }catch{
      // ignore
    }
  }

  useEffect(()=>{ refreshListCount(); }, []);
  useEffect(()=>{
      // Fetch genres and languages from metadata API
      Promise.all([
        api.get('/metadata/options/genres'),
        api.get('/metadata/options/languages')
      ]).then(([genresRes, languagesRes]) => {
        if (genresRes.data?.genres) {
          const fetchedGenres: string[] = genresRes.data.genres;
          setAvailableGenres(fetchedGenres);
          // Normalize any existing selected genres to the fetched canonical set
          setGenres((prev) => {
            const map: Record<string, string> = {
              'sci-fi': 'Science Fiction',
              'scifi': 'Science Fiction',
              'science fiction': 'Science Fiction',
              'action': 'Action',
              'comedy': 'Comedy',
              'drama': 'Drama',
              'romance': 'Romance',
              'mystery': 'Mystery',
              'thriller': 'Thriller',
              'documentary': 'Documentary',
            };
            const normalized = prev.map((g) => map[g.toLowerCase()] || g);
            const intersected = normalized.filter((g) => fetchedGenres.includes(g));
            // If intersection becomes empty, keep it empty and let validation guide user
            return intersected;
          });
        }
        if (languagesRes.data?.languages) {
          // Extract just the language codes
          const langCodes = languagesRes.data.languages.map((l: any) => l.code);
          setAvailableLanguages(langCodes);
        }
      }).catch((err) => {
        console.warn('Failed to fetch metadata options, using defaults:', err);
      setAvailableGenres(DEFAULT_GENRES);
      setAvailableLanguages(DEFAULT_LANGUAGES);
    });
  }, []);

  async function submit(){
    setMessage(""); setMessageType("");
    // inline field errors
    setErrorTitle(!title.trim() ? "Title is required" : "");
    setErrorGenres(genres.length === 0 ? "Select at least one genre" : "");
    setErrorMediaType(mediaType.length === 0 ? "Select at least one type (movie/show)" : "");
    if(!isFormValid) return;
    setIsSubmitting(true);
    const filters: any = {
      genres,
      genre_mode: genreMode,
      obscurity,
      languages,
      year_from: yearFrom,
      year_to: yearTo,
      watched_status: watchedStatus,
      not_watched_days: watchedStatus === "not_watched_recently" ? notWatchedDays : undefined,
      media_type: mediaType
    };
    const payload = {
      title,
      filters,
      item_limit: Math.min(itemLimit, maxItems)
    };
    try {
      const createRes = await api.post("/lists/", payload);
      const newId = (createRes.data && createRes.data.id) ? createRes.data.id : undefined;
      if (newId){
        // Immediately run a full sync so items (and posters) are available when opening the list
        try{ await api.post(`/lists/${newId}/sync?user_id=1&force_full=true`); }catch{}
      }
      await refreshListCount();
  setTitle(""); setGenres(DEFAULT_GENRES.slice());
      setObscurity([]); setLanguages([]); setYearFrom(2000); setYearTo(new Date().getFullYear());
      setWatchedStatus("exclude_watched"); setNotWatchedDays(90); setMediaType(["movies", "shows"]);
      if(onCreated) onCreated();
      setMessage("List created successfully"); setMessageType("success");
    } catch(e: any){
  let msg = "Unknown error";
      if (e?.response?.data?.detail) msg = e.response.data.detail;
      else if (e?.message) msg = e.message;
      else if (typeof e === 'string') msg = e;
      else if (e && typeof e === 'object') {
        // Handle [object Object] case
        msg = JSON.stringify(e);
      }
      setMessage("Error creating list: " + msg); setMessageType("error");
      console.error("Full error object:", e);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="relative z-10 bg-white/10 backdrop-blur-lg rounded-3xl shadow-2xl border border-white/20 w-full max-w-xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 bg-gradient-to-r from-purple-500 to-pink-500 rounded-xl flex items-center justify-center shadow-lg">
          <span className="text-2xl">📋</span>
        </div>
        <h3 className="text-2xl font-bold text-white">Create New List</h3>
      </div>

      {message && (
        <div className={`mb-3 p-3 rounded-xl text-sm font-medium backdrop-blur-sm ${messageType === "success" ? "bg-emerald-500/20 text-emerald-200 border border-emerald-400/30" : "bg-red-500/20 text-red-200 border border-red-400/30"}`}>{message}</div>
      )}
      <input
        value={title}
        onChange={(e)=>{ setTitle(e.target.value); setErrorTitle(e.target.value.trim() ? "" : "Title is required"); }}
        className={`w-full min-h-[44px] p-3 bg-white/5 border rounded-xl text-white placeholder-white/40 focus:ring-2 focus:ring-purple-400 focus:border-transparent transition ${errorTitle ? "border-red-400/50" : "border-white/20"}`}
        placeholder="Title (required)"
      />
      {errorTitle && <div className="text-xs text-red-300 mb-2">{errorTitle}</div>}
      <div className="mb-2 text-sm font-semibold text-white/90">Genres</div>
      <div className="flex flex-wrap gap-2 mb-1">
        {availableGenres.map(g => (
          <button key={g} onClick={()=>toggleGenre(g)} className={`min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${genres.includes(g) ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg scale-105" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}>{g}</button>
        ))}
      </div>
      <div className="flex items-center gap-4 mb-2">
        <label className="flex items-center gap-2 text-xs text-white/70 cursor-pointer">
          <input type="radio" name="genreMode" value="any" checked={genreMode==='any'} onChange={()=>setGenreMode('any')} className="accent-purple-500" />
          Match <span className="font-semibold text-white">any</span> selected genre
        </label>
        <label className="flex items-center gap-2 text-xs text-white/70 cursor-pointer">
          <input type="radio" name="genreMode" value="all" checked={genreMode==='all'} onChange={()=>setGenreMode('all')} className="accent-purple-500" />
          Match <span className="font-semibold text-white">all</span> selected genres
        </label>
      </div>
      {errorGenres && <div className="text-xs text-red-300 mb-3">{errorGenres}</div>}
      <div className="mb-2 text-sm font-semibold text-white/90">Obscurity</div>
      <div className="flex flex-wrap gap-2 mb-1">
        {["very_obscure","less_obscure","neutral","popular"].map(o => (
          <button key={o} onClick={()=>toggleObscurity(o)} className={`min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${obscurity.includes(o) ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg scale-105" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}>{o.replace("_"," ")}</button>
        ))}
      </div>
      <div className="text-xs text-white/50 mb-4">💡 Tip: Obscurity controls discovery (popular ↔ obscure) and is separate from your mood profile.</div>
      <div className="mb-2 text-sm font-semibold text-white/90">Languages</div>
      <div className="flex flex-wrap gap-2 mb-4">
        {availableLanguages.map(l => (
          <button key={l} onClick={()=>toggleLanguage(l)} className={`min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${languages.includes(l) ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg scale-105" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}>{l}</button>
        ))}
      </div>
      <div className="mb-4 grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-semibold text-white/90 mb-2">Year from</label>
          <input type="number" value={yearFrom} min={1900} max={yearTo} onChange={(e)=>setYearFrom(Number(e.target.value))} className="w-full min-h-[44px] p-3 bg-white/5 border border-white/20 rounded-xl text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent transition" />
        </div>
        <div>
          <label className="block text-sm font-semibold text-white/90 mb-2">Year to</label>
          <input type="number" value={yearTo} min={yearFrom} max={new Date().getFullYear()} onChange={(e)=>setYearTo(Number(e.target.value))} className="w-full min-h-[44px] p-3 bg-white/5 border border-white/20 rounded-xl text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent transition" />
        </div>
      </div>
      <div className="mb-4">
        <label className="block text-sm font-semibold text-white/90 mb-2">Watched status</label>
        <select value={watchedStatus} onChange={(e)=>setWatchedStatus(e.target.value)} className="w-full min-h-[44px] p-3 bg-white/5 border border-white/20 rounded-xl text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent transition">
          <option value="exclude_watched" className="bg-slate-800">Exclude already watched</option>
          <option value="include_all" className="bg-slate-800">Include all</option>
          <option value="not_watched_recently" className="bg-slate-800">Only if not watched in X days</option>
        </select>
        {watchedStatus === "not_watched_recently" && (
          <input type="number" value={notWatchedDays} min={1} max={3650} onChange={(e)=>setNotWatchedDays(Number(e.target.value))} className="w-28 min-h-[44px] p-3 bg-white/5 border border-white/20 rounded-xl text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent mt-2 transition" placeholder="Days" />
        )}
      </div>
      <div className="mb-4">
        <label className="block text-sm font-semibold text-white/90 mb-2">Type</label>
        <div className="flex gap-2 mb-1">
          <button onClick={()=>toggleMediaType("movies")} className={`flex-1 min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${mediaType.includes("movies") ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg scale-105" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}>🎬 Movies</button>
          <button onClick={()=>toggleMediaType("shows")} className={`flex-1 min-h-[44px] px-4 py-2 rounded-xl text-sm border transition-all ${mediaType.includes("shows") ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white border-transparent shadow-lg scale-105" : "bg-white/5 text-white/80 border-white/20 hover:border-purple-400 hover:bg-white/10"}`}>📺 Shows</button>
        </div>
        {errorMediaType && <div className="text-xs text-red-300">{errorMediaType}</div>}
      </div>
      <div className="mb-4">
        <label className="block text-sm font-semibold text-white/90 mb-2">Item limit</label>
        <input type="number" value={itemLimit} min={1} max={maxItems} onChange={(e)=>setItemLimit(Math.min(Number(e.target.value), maxItems))} className="w-32 min-h-[44px] p-3 bg-white/5 border border-white/20 rounded-xl text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent transition" />
        <div className="text-xs text-white/50 mt-2">Max per list: {maxItems}</div>
      </div>
      <div className="flex justify-end">
        <button
          onClick={submit}
          disabled={!isFormValid || isSubmitting || accountLoading || !canCreateMoreLists}
          className={`min-h-[44px] px-6 py-3 rounded-xl text-sm font-semibold transition-all ${(!isFormValid || isSubmitting || accountLoading || !canCreateMoreLists) ? "bg-white/10 text-white/40 cursor-not-allowed" : "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-lg hover:shadow-xl hover:scale-105"}`}
        >
          {isSubmitting 
            ? "Creating…" 
            : (accountLoading 
                ? "Checking quota…" 
                : (!canCreateMoreLists ? `Quota reached (${listCount}/${account?.max_lists ?? 0})` : "✨ Create"))}
        </button>
      </div>
    </div>
  );
}
