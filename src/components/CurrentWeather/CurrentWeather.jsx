import './CurrentWeather.css';
import WeatherIcon from '../WeatherIcon/WeatherIcon.jsx';
import { getWeatherInfo } from '../../utils/weatherCodes.js';
import { degreesToCardinal, hpaToInHg } from '../../utils/formatters.js';

function CurrentWeather({ data, lastUpdated, onRefresh }) {
  const {
    temperature_2m,
    relative_humidity_2m,
    apparent_temperature,
    weather_code,
    wind_speed_10m,
    wind_direction_10m,
    pressure_msl,
    is_day,
  } = data;

  const weatherInfo = getWeatherInfo(weather_code);
  const windCardinal = degreesToCardinal(wind_direction_10m);
  const pressureInHg = hpaToInHg(pressure_msl);

  const updatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : '';

  return (
    <div className="current-weather">
      <div className="current-weather__main">
        <WeatherIcon code={weather_code} isDay={!!is_day} size={100} />
        <div className="current-weather__temperature">
          {Math.round(temperature_2m)}&deg;F
        </div>
        <div className="current-weather__condition">
          {weatherInfo.label}
        </div>
        <div className="current-weather__feels-like">
          Feels like {Math.round(apparent_temperature)}&deg;F
        </div>
      </div>

      <div className="current-weather__details">
        <div className="current-weather__detail">
          <div className="current-weather__detail-label">Wind</div>
          <div className="current-weather__detail-value">
            {Math.round(wind_speed_10m)} mph {windCardinal}
          </div>
        </div>
        <div className="current-weather__detail">
          <div className="current-weather__detail-label">Humidity</div>
          <div className="current-weather__detail-value">
            {relative_humidity_2m}%
          </div>
        </div>
        <div className="current-weather__detail">
          <div className="current-weather__detail-label">Pressure</div>
          <div className="current-weather__detail-value">
            {pressureInHg} inHg
          </div>
        </div>
      </div>

      <div className="current-weather__footer">
        {updatedStr && (
          <span className="current-weather__updated">
            Updated {updatedStr}
          </span>
        )}
        <button className="current-weather__refresh" onClick={onRefresh}>
          Refresh
        </button>
      </div>
    </div>
  );
}

export default CurrentWeather;
