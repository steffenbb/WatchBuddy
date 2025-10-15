import { useEffect, useRef, useState } from "react";
import { api } from "../hooks/useApi";

export function useTraktAccount() {
  const [account, setAccount] = useState<{ vip: boolean; max_lists: number|null; max_items_per_list: number; message: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<number | null>(null);

  const fetchAccount = async () => {
      try {
        const r = await api.get("/lists/quota");
        const q = r.data || {};
        setAccount({
          vip: !!q.vip,
          max_lists: q.max_lists ?? null,
          max_items_per_list: q.max_items_per_list ?? (q.vip ? 5000 : 100),
          message: q.message || (q.vip ? "VIP: Unlimited lists, 5000 items each" : "Free: Up to 2 lists, 100 items each")
        });
      } catch {
        setAccount({ vip: false, max_lists: 2, max_items_per_list: 100, message: "Free: Up to 2 lists, 100 items each" });
      } finally {
        setLoading(false);
      }
    };

  useEffect(() => {
    fetchAccount();
    // Poll every 60s to detect VIP status changes
    timerRef.current = window.setInterval(fetchAccount, 60000) as unknown as number;
    return () => { if (timerRef.current) window.clearInterval(timerRef.current); };
  }, []);

  return { account, loading, refreshQuota: fetchAccount };
}
