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
    <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 bg-gradient-to-r from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
          <span className="text-white text-sm font-bold">📋</span>
        </div>
        <h3 className="text-xl font-bold text-gray-800">Create New List</h3>
      </div>

      {message && (
        <div className={`mb-3 p-3 rounded-lg text-sm font-medium ${messageType === "success" ? "bg-green-100 text-green-800 border border-green-200" : "bg-red-100 text-red-800 border border-red-200"}`}>{message}</div>
      )}
      <input
        value={title}
        onChange={(e)=>{ setTitle(e.target.value); setErrorTitle(e.target.value.trim() ? "" : "Title is required"); }}
        className={`w-full p-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent mb-1 ${errorTitle ? "border-red-500" : "border-gray-300"}`}
        placeholder="Title (required)"
      />
      {errorTitle && <div className="text-xs text-red-600 mb-2">{errorTitle}</div>}
      <div className="mb-2 text-sm font-semibold text-gray-700">Genres</div>
      <div className="flex flex-wrap gap-2 mb-1">
        {availableGenres.map(g => (
          <button key={g} onClick={()=>toggleGenre(g)} className={`px-3 py-1.5 rounded-lg text-sm border transition ${genres.includes(g) ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-gray-700 border-gray-300 hover:border-indigo-400"}`}>{g}</button>
        ))}
      </div>
      <div className="flex items-center gap-4 mb-2">
        <label className="flex items-center gap-1 text-xs cursor-pointer">
          <input type="radio" name="genreMode" value="any" checked={genreMode==='any'} onChange={()=>setGenreMode('any')} />
          Match <span className="font-semibold">any</span> selected genre
        </label>
        <label className="flex items-center gap-1 text-xs cursor-pointer">
          <input type="radio" name="genreMode" value="all" checked={genreMode==='all'} onChange={()=>setGenreMode('all')} />
          Match <span className="font-semibold">all</span> selected genres
        </label>
      </div>
      {errorGenres && <div className="text-xs text-red-600 mb-3">{errorGenres}</div>}
      <div className="mb-2 text-sm font-semibold text-gray-700">Obscurity</div>
      <div className="flex flex-wrap gap-2 mb-1">
        {["very_obscure","less_obscure","neutral","popular"].map(o => (
          <button key={o} onClick={()=>toggleObscurity(o)} className={`px-3 py-1.5 rounded-lg text-sm border transition ${obscurity.includes(o) ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-gray-700 border-gray-300 hover:border-indigo-400"}`}>{o.replace("_"," ")}</button>
        ))}
      </div>
      <div className="text-xs text-gray-500 mb-4">Tip: Obscurity controls discovery (popular ↔ obscure) and is separate from your mood profile.</div>
      <div className="mb-2 text-sm font-semibold text-gray-700">Languages</div>
      <div className="flex flex-wrap gap-2 mb-4">
        {availableLanguages.map(l => (
          <button key={l} onClick={()=>toggleLanguage(l)} className={`px-3 py-1.5 rounded-lg text-sm border transition ${languages.includes(l) ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-gray-700 border-gray-300 hover:border-indigo-400"}`}>{l}</button>
        ))}
      </div>
      <div className="mb-4 grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1">Year from</label>
          <input type="number" value={yearFrom} min={1900} max={yearTo} onChange={(e)=>setYearFrom(Number(e.target.value))} className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
        </div>
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-1">Year to</label>
          <input type="number" value={yearTo} min={yearFrom} max={new Date().getFullYear()} onChange={(e)=>setYearTo(Number(e.target.value))} className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
        </div>
      </div>
      <div className="mb-4">
        <label className="block text-sm font-semibold text-gray-700 mb-1">Watched status</label>
        <select value={watchedStatus} onChange={(e)=>setWatchedStatus(e.target.value)} className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent">
          <option value="exclude_watched">Exclude already watched</option>
          <option value="include_all">Include all</option>
          <option value="not_watched_recently">Only if not watched in X days</option>
        </select>
        {watchedStatus === "not_watched_recently" && (
          <input type="number" value={notWatchedDays} min={1} max={3650} onChange={(e)=>setNotWatchedDays(Number(e.target.value))} className="w-24 p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent mt-2" placeholder="Days" />
        )}
      </div>
      <div className="mb-4">
        <label className="block text-sm font-semibold text-gray-700 mb-1">Type</label>
        <div className="flex gap-2 mb-1">
          <button onClick={()=>toggleMediaType("movies")} className={`px-3 py-1.5 rounded-lg text-sm border transition ${mediaType.includes("movies") ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-gray-700 border-gray-300 hover:border-indigo-400"}`}>Movies</button>
          <button onClick={()=>toggleMediaType("shows")} className={`px-3 py-1.5 rounded-lg text-sm border transition ${mediaType.includes("shows") ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-gray-700 border-gray-300 hover:border-indigo-400"}`}>Shows</button>
        </div>
        {errorMediaType && <div className="text-xs text-red-600">{errorMediaType}</div>}
      </div>
      <div className="mb-4">
        <label className="block text-sm font-semibold text-gray-700 mb-1">Item limit</label>
        <input type="number" value={itemLimit} min={1} max={maxItems} onChange={(e)=>setItemLimit(Math.min(Number(e.target.value), maxItems))} className="w-28 p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
        <div className="text-xs text-gray-500 mt-1">Max per list: {maxItems}</div>
      </div>
      <div className="flex justify-end">
        <button
          onClick={submit}
          disabled={!isFormValid || isSubmitting || accountLoading || !canCreateMoreLists}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${(!isFormValid || isSubmitting || accountLoading || !canCreateMoreLists) ? "bg-indigo-300 text-white cursor-not-allowed" : "bg-indigo-600 text-white hover:bg-indigo-700"}`}
        >
          {isSubmitting 
            ? "Creating…" 
            : (accountLoading 
                ? "Checking quota…" 
                : (!canCreateMoreLists ? `Quota reached (${listCount}/${account?.max_lists ?? 0})` : "Create"))}
        </button>
      </div>
    </div>
  );
}
