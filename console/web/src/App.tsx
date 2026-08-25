import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { HomePage } from "./pages/HomePage";
import { SessionsPage } from "./pages/SessionsPage";
import { AskPage } from "./pages/AskPage";
import { RegistryPage } from "./pages/RegistryPage";
import { RegistryDetailPage } from "./pages/RegistryDetailPage";
import { HostWiringPage } from "./pages/HostWiringPage";

export function App() {
  return (
    <BrowserRouter>
      <div className="layout">
        <nav className="nav">
          <Link to="/">Connection</Link>
          <Link to="/sessions">Sessions</Link>
          <Link to="/registry">Registry</Link>
          <Link to="/host-wiring">Host wiring</Link>
        </nav>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:handle/ask" element={<AskPage />} />
          <Route path="/registry" element={<RegistryPage />} />
          <Route path="/registry/:pluginId" element={<RegistryDetailPage />} />
          <Route path="/host-wiring" element={<HostWiringPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
