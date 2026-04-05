import { useState, useEffect, useCallback, useRef } from "react";
import { fetchCurrentWeather } from "../api/weather.js";
import { REFRESH_INTERVAL_MS } from "../config.js";

export default function useWeather({ latitude, longitude }) {
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const intervalRef = useRef(null);

  const refresh = useCallback(async () => {
    if (latitude == null || longitude == null) return;

    setLoading(true);
    setError(null);

    try {
      const data = await fetchCurrentWeather(latitude, longitude);
      setCurrent(data.current);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [latitude, longitude]);

  useEffect(() => {
    refresh();

    intervalRef.current = setInterval(refresh, REFRESH_INTERVAL_MS);

    return () => {
      clearInterval(intervalRef.current);
    };
  }, [refresh]);

  return { current, loading, error, refresh, lastUpdated };
}
