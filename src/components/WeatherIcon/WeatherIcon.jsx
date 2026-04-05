import './WeatherIcon.css';

const SUN_COLOR = '#FFD700';
const MOON_COLOR = '#E8E8E8';
const CLOUD_COLOR = '#B0BEC5';
const RAIN_COLOR = '#42A5F5';
const SNOW_COLOR = '#E3F2FD';
const LIGHTNING_COLOR = '#FFC107';

function Sun({ cx, cy, r }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill={SUN_COLOR} />
      {[0, 45, 90, 135, 180, 225, 270, 315].map((angle) => {
        const rad = (angle * Math.PI) / 180;
        const x1 = cx + (r + 4) * Math.cos(rad);
        const y1 = cy + (r + 4) * Math.sin(rad);
        const x2 = cx + (r + 10) * Math.cos(rad);
        const y2 = cy + (r + 10) * Math.sin(rad);
        return (
          <line
            key={angle}
            x1={x1} y1={y1} x2={x2} y2={y2}
            stroke={SUN_COLOR} strokeWidth="3" strokeLinecap="round"
          />
        );
      })}
    </g>
  );
}

function Moon({ cx, cy, r }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill={MOON_COLOR} />
      <circle cx={cx + r * 0.35} cy={cy - r * 0.25} r={r * 0.8} fill="currentColor" style={{ color: 'var(--color-bg, #f0f4f8)' }} />
    </g>
  );
}

function Cloud({ cx, cy, scale = 1, color = CLOUD_COLOR }) {
  return (
    <g transform={`translate(${cx}, ${cy}) scale(${scale})`}>
      <circle cx="-10" cy="0" r="14" fill={color} />
      <circle cx="8" cy="-6" r="18" fill={color} />
      <circle cx="24" cy="0" r="14" fill={color} />
      <rect x="-16" y="0" width="54" height="14" rx="4" fill={color} />
    </g>
  );
}

function Raindrops({ cx, cy, count = 3, heavy = false }) {
  const drops = [];
  const spacing = heavy ? 10 : 14;
  const startX = cx - ((count - 1) * spacing) / 2;
  for (let i = 0; i < count; i++) {
    const x = startX + i * spacing;
    drops.push(
      <line
        key={i}
        x1={x} y1={cy}
        x2={x - 3} y2={cy + (heavy ? 12 : 8)}
        stroke={RAIN_COLOR} strokeWidth={heavy ? 3 : 2} strokeLinecap="round"
      />
    );
  }
  return <g>{drops}</g>;
}

function Snowflakes({ cx, cy, count = 3 }) {
  const flakes = [];
  const spacing = 14;
  const startX = cx - ((count - 1) * spacing) / 2;
  for (let i = 0; i < count; i++) {
    const x = startX + i * spacing;
    const y = cy + (i % 2 === 0 ? 0 : 6);
    flakes.push(
      <g key={i} transform={`translate(${x}, ${y})`}>
        <line x1="0" y1="-5" x2="0" y2="5" stroke={SNOW_COLOR} strokeWidth="1.5" strokeLinecap="round" />
        <line x1="-4.3" y1="-2.5" x2="4.3" y2="2.5" stroke={SNOW_COLOR} strokeWidth="1.5" strokeLinecap="round" />
        <line x1="-4.3" y1="2.5" x2="4.3" y2="-2.5" stroke={SNOW_COLOR} strokeWidth="1.5" strokeLinecap="round" />
      </g>
    );
  }
  return <g>{flakes}</g>;
}

function Lightning({ cx, cy }) {
  return (
    <polygon
      points={`${cx - 3},${cy} ${cx + 4},${cy} ${cx},${cy + 10} ${cx + 8},${cy + 10} ${cx - 2},${cy + 24} ${cx + 2},${cy + 14} ${cx - 5},${cy + 14}`}
      fill={LIGHTNING_COLOR}
    />
  );
}

function FogLines({ cx, cy }) {
  return (
    <g>
      {[0, 8, 16].map((offset) => (
        <line
          key={offset}
          x1={cx - 20} y1={cy + offset}
          x2={cx + 20} y2={cy + offset}
          stroke={CLOUD_COLOR} strokeWidth="3" strokeLinecap="round"
          opacity={1 - offset * 0.02}
        />
      ))}
    </g>
  );
}

function getCondition(code) {
  if (code === 0 || code === 1) return 'clear';
  if (code === 2) return 'partly-cloudy';
  if (code === 3) return 'overcast';
  if (code === 45 || code === 48) return 'fog';
  if (code >= 51 && code <= 57) return 'drizzle';
  if (code >= 61 && code <= 63) return 'rain';
  if (code === 65 || code === 66 || code === 67) return 'heavy-rain';
  if (code >= 80 && code <= 82) return 'rain';
  if (code === 71 || code === 73 || code === 77) return 'snow';
  if (code === 75 || code === 85 || code === 86) return 'heavy-snow';
  if (code >= 95) return 'thunderstorm';
  return 'clear';
}

function WeatherIcon({ code, isDay = true, size = 80 }) {
  const condition = getCondition(code);
  const vb = '0 0 80 80';

  const renderIcon = () => {
    switch (condition) {
      case 'clear':
        return isDay
          ? <Sun cx={40} cy={38} r={16} />
          : <Moon cx={40} cy={38} r={16} />;

      case 'partly-cloudy':
        return isDay ? (
          <g>
            <Sun cx={32} cy={28} r={12} />
            <Cloud cx={38} cy={44} scale={0.85} />
          </g>
        ) : (
          <g>
            <Moon cx={32} cy={28} r={12} />
            <Cloud cx={38} cy={44} scale={0.85} />
          </g>
        );

      case 'overcast':
        return (
          <g>
            <Cloud cx={30} cy={28} scale={0.7} color="#CFD8DC" />
            <Cloud cx={38} cy={40} scale={0.95} />
          </g>
        );

      case 'fog':
        return (
          <g>
            <Cloud cx={38} cy={28} scale={0.8} />
            <FogLines cx={40} cy={48} />
          </g>
        );

      case 'drizzle':
        return (
          <g>
            <Cloud cx={38} cy={30} scale={0.9} />
            <Raindrops cx={40} cy={52} count={2} />
          </g>
        );

      case 'rain':
        return (
          <g>
            <Cloud cx={38} cy={28} scale={0.9} />
            <Raindrops cx={40} cy={50} count={3} />
          </g>
        );

      case 'heavy-rain':
        return (
          <g>
            <Cloud cx={38} cy={26} scale={0.95} />
            <Raindrops cx={40} cy={48} count={4} heavy />
          </g>
        );

      case 'snow':
        return (
          <g>
            <Cloud cx={38} cy={28} scale={0.9} />
            <Snowflakes cx={40} cy={52} count={3} />
          </g>
        );

      case 'heavy-snow':
        return (
          <g>
            <Cloud cx={38} cy={26} scale={0.95} />
            <Snowflakes cx={40} cy={48} count={4} />
          </g>
        );

      case 'thunderstorm':
        return (
          <g>
            <Cloud cx={38} cy={24} scale={0.95} color="#90A4AE" />
            <Lightning cx={38} cy={44} />
            <Raindrops cx={40} cy={50} count={2} />
          </g>
        );

      default:
        return <Sun cx={40} cy={38} r={16} />;
    }
  };

  return (
    <div className="weather-icon">
      <svg
        width={size}
        height={size}
        viewBox={vb}
        xmlns="http://www.w3.org/2000/svg"
      >
        {renderIcon()}
      </svg>
    </div>
  );
}

export default WeatherIcon;
