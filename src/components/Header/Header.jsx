import './Header.css';

function Header({ locationName }) {
  return (
    <header className="header">
      <h1 className="header__title">Most Accurate Weather</h1>
      {locationName && <p className="header__location">{locationName}</p>}
    </header>
  );
}

export default Header;
