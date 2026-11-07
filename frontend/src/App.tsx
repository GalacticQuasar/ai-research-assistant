import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import LibraryPage from './pages/LibraryPage'
import PaperDetailPage from './pages/PaperDetailPage'
import SearchPage from './pages/SearchPage'

function App() {
  return (
    <BrowserRouter>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            Research Assistant
          </Link>
          <nav>
            <NavLink to="/" end>
              Search
            </NavLink>
            <NavLink to="/library">Library</NavLink>
          </nav>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/papers/:id" element={<PaperDetailPage />} />
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}

export default App
