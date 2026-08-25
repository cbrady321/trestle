import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { HomePage } from "./pages/HomePage";
import { SessionsPage } from "./pages/SessionsPage";
import { AskPage } from "./pages/AskPage";

export function App() {
  return (
    <BrowserRouter>
      <div className="layout">
        <nav className="nav">
          <Link to="/">Connection</Link>
          <Link to="/sessions">Sessions</Link>
        </nav>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:handle/ask" element={<AskPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
