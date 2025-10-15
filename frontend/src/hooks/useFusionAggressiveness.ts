import { useEffect, useState } from "react";

export function useFusionAggressiveness() {
  const [aggressiveness, setAggressiveness] = useState<number>(1);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/settings/fusion")
      .then((res) => res.json())
      .then((data) => {
        if (typeof data.aggressiveness === "number") setAggressiveness(data.aggressiveness);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return { aggressiveness, loading };
}
