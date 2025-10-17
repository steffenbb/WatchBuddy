import React from "react";
import { api } from "../hooks/useApi";
import { useTraktAccount } from "../hooks/useTraktAccount";

export default function SmartListsPanel({ onCreate }: { onCreate?: ()=>void }){
  const { account, loading: accountLoading } = useTraktAccount();
  const [count, setCount] = React.useState<number>(1);
  const [interval, setInterval] = React.useState<number>(0); // in minutes
  const [autoRefresh, setAutoRefresh] = React.useState<boolean>(false);
  const [fusionMode, setFusionMode] = React.useState<boolean>(false);
  const [listType, setListType] = React.useState<string>("smartlist");
  const [discovery, setDiscovery] = React.useState<string>("balanced");
  const [itemsPerList, setItemsPerList] = React.useState<number>(20);

  const [message, setMessage] = React.useState<string>("");
  const [messageType, setMessageType] = React.useState<"success"|"error"|"">("");
  const [isLoading, setIsLoading] = React.useState<boolean>(false);
  const [listCount, setListCount] = React.useState<number>(0);

  const canCreateMoreLists = React.useMemo(()=>{
    if (!account) return false;
    if (account.vip) return true;
    if (account.max_lists === null || account.max_lists === undefined) return true;
    return listCount < account.max_lists;
  }, [account, listCount]);

  async function refreshListCount(){
    try{
      const res = await api.get('/lists/');
      const data = Array.isArray(res.data) ? res.data : (res.data?.lists || []);
      setListCount(data.length || 0);
    }catch{}
  }

  React.useEffect(()=>{ refreshListCount(); }, []);

  async function createSmart(){
    setMessage(""); setMessageType(""); setIsLoading(true);
    try {
      if (!canCreateMoreLists) {
        throw new Error(`Quota reached (${listCount}/${account?.max_lists ?? 0}). Upgrade to VIP for unlimited lists.`);
      }
      await api.post("/smartlists/create", { 
        count, 
        auto_refresh: autoRefresh, 
        interval,
        fusion_mode: fusionMode,
        list_type: listType,
        discovery,
        media_types: ["movies","shows"],
        items_per_list: itemsPerList,
        user_id: 1 // Always use user_id=1 for demo
      });
      if(onCreate) onCreate();
      await refreshListCount();
      setMessage("SmartList created successfully"); setMessageType("success");
    } catch(e: any){
      let msg = "Unknown error";
      if (e?.response?.data?.detail) msg = e.response.data.detail;
      else if (e?.message) msg = e.message;
      else if (typeof e === 'string') msg = e;
      else if (e && typeof e === 'object') {
        // Handle [object Object] case
        msg = JSON.stringify(e);
      }
      setMessage("Error creating smartlist: " + msg); setMessageType("error");
      console.error("Full error object:", e);
    } finally {
      setIsLoading(false);
    }
  }

  const listTypeDescriptions = {
    smartlist: "AI-powered recommendations using advanced scoring algorithms, mood analysis, and semantic understanding",
    discovery: "Explore hidden gems and obscure content you might have missed",
    trending: "Currently popular content across the Trakt community"
  };

  return (
    <div className="relative z-10 bg-white/90 backdrop-blur-xl rounded-3xl shadow-2xl border border-indigo-100 w-full max-w-xl mx-auto p-6 md:p-8 flex flex-col gap-4 transition-all duration-500">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 bg-gradient-to-r from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center">
          <span className="text-white text-sm font-bold">✨</span>
        </div>
        <h4 className="text-xl font-bold text-gray-800">Smart Lists</h4>
      </div>
      
      {message && (
        <div className={`mb-4 p-3 rounded-lg text-sm font-medium ${
          messageType === "success" 
            ? "bg-green-100 text-green-800 border border-green-200" 
            : "bg-red-100 text-red-800 border border-red-200"
        }`}>
          {message}
        </div>
      )}

      {/* List Type Selection */}
      <div className="mb-6">
        <label className="block text-sm font-semibold text-gray-700 mb-2">List Type</label>
        <div className="space-y-3">
          {Object.entries(listTypeDescriptions).map(([type, description]) => (
            <div key={type} className="flex items-start gap-3">
              <input
                type="radio"
                id={type}
                name="listType"
                value={type}
                checked={listType === type}
                onChange={(e) => setListType(e.target.value)}
                className="mt-1 w-4 h-4 text-blue-600 border-gray-300 focus:ring-blue-500"
              />
              <div className="flex-1">
                <label htmlFor={type} className="block text-sm font-medium text-gray-800 capitalize cursor-pointer">
                  {type.replace(/([A-Z])/g, ' $1').trim()}
                </label>
                <p className="text-xs text-gray-600 mt-1">{description}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Fusion Mode Toggle */}
      {listType === "smartlist" && (
        <div className="mb-6 p-4 bg-white rounded-lg border border-blue-200">
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="fusionMode"
              checked={fusionMode}
              onChange={(e) => setFusionMode(e.target.checked)}
              className="w-4 h-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
            />
            <div className="flex-1">
              <label htmlFor="fusionMode" className="text-sm font-semibold text-gray-800 cursor-pointer">
                Fusion Mode ⚡
              </label>
              <p className="text-xs text-gray-600 mt-1">
                Blend multiple recommendation algorithms for more diverse and surprising results. 
                Combines collaborative filtering, content-based analysis, and trending data.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Configuration Options */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Number of Lists</label>
          <input
            type="number"
            min={1}
            max={5}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Items per List</label>
          <select
            value={itemsPerList}
            onChange={(e) => setItemsPerList(Number(e.target.value))}
            className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          >
            <option value={10}>10 items</option>
            <option value={20}>20 items</option>
            <option value={50}>50 items</option>
          </select>
        </div>
      </div>

      {/* Discovery Strategy */}
      <div className="mb-6">
        <label className="block text-sm font-semibold text-gray-700 mb-2">Discovery Strategy</label>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { key: "balanced", label: "Balanced" },
            { key: "obscure", label: "Obscure" },
            { key: "popular", label: "Popular" },
            { key: "very_obscure", label: "Very Obscure" },
          ].map(opt => (
            <button
              key={opt.key}
              onClick={() => setDiscovery(opt.key)}
              className={`px-3 py-2 rounded-lg border text-sm font-medium ${discovery===opt.key ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'}`}
              type="button"
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-gray-500 mt-2">Balanced mixes sources. Obscure emphasizes hidden gems. Popular leans trending/mainstream.</p>
      </div>

      {/* Auto-refresh Options */}
      <div className="mb-6 p-4 bg-white rounded-lg border border-blue-200">
        <div className="flex items-center gap-3 mb-3">
          <input
            type="checkbox"
            id="autoRefresh"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
          />
          <label htmlFor="autoRefresh" className="text-sm font-semibold text-gray-800 cursor-pointer">
            Auto-refresh Lists
          </label>
        </div>
        {autoRefresh && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Refresh Interval (minutes)
            </label>
            <input
              type="number"
              min={15}
              max={1440}
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
              className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              placeholder="60"
            />
            <p className="text-xs text-gray-500 mt-1">
              Minimum 15 minutes. Lists will update automatically based on your latest activity.
            </p>
          </div>
        )}
      </div>

      {/* Generate Button */}
      <button
        onClick={createSmart}
        disabled={isLoading || accountLoading || !canCreateMoreLists}
        className={`w-full py-3 px-4 rounded-lg font-semibold text-white transition-all duration-200 ${
          (isLoading || accountLoading || !canCreateMoreLists)
            ? "bg-gray-400 cursor-not-allowed"
            : "bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 shadow-lg hover:shadow-xl transform hover:scale-[1.02] active:scale-[0.98]"
        }`}
      >
        {isLoading ? (
          <div className="flex items-center justify-center gap-2">
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            Generating...
          </div>
        ) : (accountLoading ? (
          <div className="flex items-center justify-center gap-2">
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            Checking quota…
          </div>
        ) : (!canCreateMoreLists ? (
          <span>Quota reached ({listCount}/{account?.max_lists ?? 0})</span>
        ) : (
          <span>Generate {count} Smart List{count > 1 ? 's' : ''}</span>
        )))}
      </button>

      {/* Help Text */}
      <div className="mt-4 p-3 bg-blue-50 rounded-lg border border-blue-100">
        <p className="text-xs text-blue-700">
          <strong>Tip:</strong> Smart Lists learn from your viewing history and preferences. 
          The more you watch and rate content, the better your recommendations become!
        </p>
      </div>
    </div>
  );
}
