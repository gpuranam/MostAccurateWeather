import './App.css';
import useWeather from './hooks/useWeather.js';
import { DEFAULT_LOCATION } from './config.js';
import Header from './components/Header/Header.jsx';
import CurrentWeather from './components/CurrentWeather/CurrentWeather.jsx';
import LoadingSpinner from './components/LoadingSpinner/LoadingSpinner.jsx';

function App() {
  const { current, loading, error, refresh, lastUpdated } = useWeather({
    latitude: DEFAULT_LOCATION.latitude,
    longitude: DEFAULT_LOCATION.longitude,
  });

  return (
    <div className="app">
      <Header locationName={DEFAULT_LOCATION.name} />
      {loading && !current && <LoadingSpinner />}
      {error && (
        <div className="error-container">
          <p>{error}</p>
          <button onClick={refresh}>Retry</button>
        </div>
      )}
      {current && (
        <CurrentWeather
          data={current}
          lastUpdated={lastUpdated}
          onRefresh={refresh}
        />
      )}
    </div>
  );
}

export default App;
