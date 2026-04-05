import './LoadingSpinner.css';

function LoadingSpinner() {
  return (
    <div className="loading-spinner">
      <div className="loading-spinner__circle" />
      <p className="loading-spinner__text">Loading weather data...</p>
    </div>
  );
}

export default LoadingSpinner;
