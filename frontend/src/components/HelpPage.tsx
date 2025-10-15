import React from "react";

export default function HelpPage() {
  return (
    <div className="prose max-w-2xl mx-auto bg-white/90 rounded-2xl shadow p-6 my-8">
      <h1>WatchBuddy Help & Wiki</h1>
      <p>Welcome to WatchBuddy! This page explains all major features, filters, and algorithms in the app. Please keep this page updated as new features are added.</p>
      <h2>Features Overview</h2>
      <ul>
        <li><b>SmartLists</b>: AI-powered lists that use advanced scoring, mood, and semantic analysis.</li>
        <li><b>Suggested Lists</b>: Curated recommendations based on your preferences and history.</li>
        <li><b>Manual Lists</b>: Custom lists you build yourself.</li>
        <li><b>Ratings</b>: Thumbs up/down to improve recommendations.</li>
        <li><b>Dynamic Titles</b>: Netflix-style titles that adapt to your taste.</li>
        <li><b>Notifications</b>: Alerts for syncs, new suggestions, and more.</li>
      </ul>
      <h2>Filters & Options</h2>
      <ul>
        <li><b>Genres</b>: Select one or more genres. Use the 'Match any/all' toggle to control strictness.</li>
        <li><b>Obscurity</b>: Choose between popular, obscure, or balanced content.</li>
        <li><b>Languages</b>: Filter by original language.</li>
        <li><b>Year Range</b>: Limit results to a specific release year range.</li>
        <li><b>Watched Status</b>: Exclude watched, include all, or only items not watched recently.</li>
        <li><b>Media Types</b>: Movies, shows, or both.</li>
      </ul>
      <h2>Algorithms & Scoring</h2>
      <ul>
        <li><b>Candidate Pool</b>: All list types use a broad, blended pool of recommendations (trending, popular, obscure, personalized, search).</li>
        <li><b>Scoring</b>: Each item is scored based on genre match, semantic similarity, mood, rating, novelty, and popularity.</li>
        <li><b>Fusion Mode</b>: Combines collaborative filtering, content-based, and trending data for best results.</li>
        <li><b>Mood Analysis</b>: (Planned) Will use your recent activity and ratings to suggest content that fits your mood.</li>
      </ul>
      <h2>Tips & Hints</h2>
      <ul>
        <li>Use thumbs up/down to improve your recommendations.</li>
        <li>Edit SmartLists to adjust filters and see instant changes.</li>
        <li>Click the logo to return to the Dashboard.</li>
        <li>Check the status widgets for API health.</li>
      </ul>
      <h2>FAQ</h2>
      <ul>
        <li><b>Why is my time zone off?</b><br/>See the Time Zone Correction section in settings (coming soon).</li>
        <li><b>How do I share a list?</b><br/>List sharing is coming soon!</li>
  <li><b>How do I regenerate suggestions?</b><br/>Remove a suggestion and a new one will be generated instantly. Suggested lists now fully support removal and refresh.</li>
      </ul>
      <hr/>
      <div className="text-xs text-gray-500">Last updated: October 13, 2025</div>
    </div>
  );
}
