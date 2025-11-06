import React, { useState } from "react";

interface Section {
  id: string;
  title: string;
  icon: string;
  content: JSX.Element;
}

export default function HelpPage() {
  const [activeSection, setActiveSection] = useState<string>("getting-started");

  const sections: Section[] = [
    {
      id: "getting-started",
      title: "Getting Started",
      icon: "🚀",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">First-Time Setup</h3>
          <ol className="space-y-3 text-white/90">
            <li className="flex items-start gap-3">
              <span className="text-purple-400 font-bold">1.</span>
              <div>
                <strong>Connect Trakt:</strong> Go to Settings → Trakt Authentication → Authorize with Trakt
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="text-purple-400 font-bold">2.</span>
              <div>
                <strong>Add TMDB API Key:</strong> Settings → TMDB API Key (get free key from themoviedb.org)
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="text-purple-400 font-bold">3.</span>
              <div>
                <strong>Create Your First List:</strong> Choose from AI Lists, SmartLists, or Individual Lists
              </div>
            </li>
          </ol>
          <div className="bg-white/10 backdrop-blur-sm border border-white/20 rounded-xl p-4 mt-6">
            <h4 className="font-semibold text-white mb-2">💡 Pro Tip</h4>
            <p className="text-white/80">Start with AI Lists for quick recommendations, then create SmartLists for more control over filters and preferences.</p>
          </div>
        </div>
      ),
    },
    {
      id: "ai-lists",
      title: "AI Lists",
      icon: "✨",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">AI-Powered Recommendations</h3>
          <p className="text-white/90">Create personalized lists using natural language prompts. Our AI understands your preferences and finds perfect matches.</p>
          
          <h4 className="font-semibold text-white mt-6 mb-2">Example Prompts:</h4>
          <ul className="space-y-2 text-white/80">
            <li className="flex items-start gap-2">
              <span className="text-pink-400">•</span>
              <span>"Dark sci-fi thrillers from the 90s"</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-pink-400">•</span>
              <span>"Feel-good romantic comedies like Notting Hill"</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-pink-400">•</span>
              <span>"Mind-bending movies without horror"</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-pink-400">•</span>
              <span>"Danish crime dramas"</span>
            </li>
          </ul>

          <h4 className="font-semibold text-white mt-6 mb-2">Features:</h4>
          <ul className="space-y-2 text-white/80">
            <li className="flex items-start gap-2">
              <span>✓</span>
              <span><strong>Semantic Search:</strong> Understands meaning, not just keywords (20k FAISS candidate pool)</span>
            </li>
            <li className="flex items-start gap-2">
              <span>✓</span>
              <span><strong>Mood Detection:</strong> Identifies tone and atmosphere</span>
            </li>
            <li className="flex items-start gap-2">
              <span>✓</span>
              <span><strong>Smart Filtering:</strong> Extracts genres, years, languages, networks, countries, creators, and directors automatically</span>
            </li>
            <li className="flex items-start gap-2">
              <span>✓</span>
              <span><strong>Enhanced Filters:</strong> Match by TV networks, production countries, show creators, and movie directors</span>
            </li>
            <li className="flex items-start gap-2">
              <span>✓</span>
              <span><strong>Dynamic 7 Lists:</strong> Generate 7 mood-based lists instantly</span>
            </li>
            <li className="flex items-start gap-2">
              <span>✓</span>
              <span><strong>Memory Optimized:</strong> Handles large pools efficiently with smart batching and garbage collection</span>
            </li>
          </ul>
        </div>
      ),
    },
    {
      id: "smartlists",
      title: "SmartLists",
      icon: "🎯",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">Filter-Based Recommendations</h3>
          <p className="text-white/90">SmartLists give you fine-grained control with powerful filters and automatic updates.</p>

          <h4 className="font-semibold text-white mt-6 mb-2">Available Filters:</h4>
          <div className="space-y-4">
            <div className="bg-white/5 backdrop-blur-sm rounded-lg p-4">
              <h5 className="font-semibold text-white mb-2">� Networks & Countries</h5>
              <p className="text-white/80 text-sm mb-2">Filter by TV networks (HBO, Netflix) and production countries</p>
              <p className="text-white/70 text-xs">Perfect for finding region-specific content or network originals</p>
            </div>

            <div className="bg-white/5 backdrop-blur-sm rounded-lg p-4">
              <h5 className="font-semibold text-white mb-2">👥 Creators & Directors</h5>
              <p className="text-white/80 text-sm mb-2">Match by show creators and movie directors</p>
              <p className="text-white/70 text-xs">Find all works by your favorite creators across TV and film</p>
            </div>

            <div className="bg-white/5 backdrop-blur-sm rounded-lg p-4">
              <h5 className="font-semibold text-white mb-2">�📽️ Genres</h5>
              <p className="text-white/80 text-sm mb-2">Action, Comedy, Drama, Horror, Sci-Fi, and 15+ more</p>
              <p className="text-white/70 text-xs">Match Mode: Any (at least one) or All (must have all selected)</p>
            </div>

            <div className="bg-white/5 backdrop-blur-sm rounded-lg p-4">
              <h5 className="font-semibold text-white mb-2">🎭 Moods</h5>
              <p className="text-white/80 text-sm mb-2">Dark, Cozy, Tense, Quirky, Epic, Intimate (up to 3)</p>
              <p className="text-white/70 text-xs">AI analyzes tone and atmosphere to match your mood</p>
            </div>

            <div className="bg-white/5 backdrop-blur-sm rounded-lg p-4">
              <h5 className="font-semibold text-white mb-2">🌍 Languages</h5>
              <p className="text-white/80 text-sm mb-2">20+ languages including Danish, Swedish, Japanese, Korean</p>
              <p className="text-white/70 text-xs">Smart fallback when content is scarce</p>
            </div>

            <div className="bg-white/5 backdrop-blur-sm rounded-lg p-4">
              <h5 className="font-semibold text-white mb-2">🔍 Discovery Mode</h5>
              <p className="text-white/80 text-sm">Mainstream → Balanced → Hidden Gems → Ultra Discovery</p>
            </div>
          </div>
        </div>
      ),
    },
    {
      id: "individual-lists",
      title: "Individual Lists",
      icon: "📝",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">Manually Curated Lists</h3>
          <p className="text-white/90">Full control to build your perfect watchlist. Search, add, and organize exactly what you want.</p>

          <h4 className="font-semibold text-white mt-6 mb-2">Features:</h4>
          <ul className="space-y-3 text-white/80">
            <li className="flex items-start gap-3">
              <span className="text-purple-400">🔎</span>
              <div>
                <strong>Hybrid Search:</strong> Combines semantic (FAISS) and literal (ElasticSearch) search
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="text-purple-400">✨</span>
              <div>
                <strong>FAISS Suggestions:</strong> Get smart recommendations based on your current list items
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="text-purple-400">🎯</span>
              <div>
                <strong>Fit Scoring:</strong> See how well each item matches your profile (0-100%)
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="text-purple-400">↕️</span>
              <div>
                <strong>Drag & Drop:</strong> Reorder items however you like
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="text-purple-400">🔄</span>
              <div>
                <strong>Manual Trakt Sync:</strong> Push to Trakt when you're ready
              </div>
            </li>
          </ul>

          <div className="bg-white/10 backdrop-blur-sm border border-white/20 rounded-xl p-4 mt-6">
            <h4 className="font-semibold text-white mb-2">💡 Use Cases</h4>
            <ul className="text-white/80 text-sm space-y-1">
              <li>• Build themed watchlists ("Best of 2024", "Comfort Movies")</li>
              <li>• Share curated collections with friends via Trakt</li>
              <li>• Organize movies by mood or occasion</li>
            </ul>
          </div>
        </div>
      ),
    },
    {
      id: "features",
      title: "Features",
      icon: "⚡",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">Powerful Features</h3>

          <div className="space-y-4">
            <div className="bg-gradient-to-r from-purple-500/20 to-pink-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">✓ Watched Status Sync</h4>
              <p className="text-white/80 text-sm">Automatically syncs with Trakt. Green checkmarks show what you've watched, with watch dates displayed.</p>
            </div>

            <div className="bg-gradient-to-r from-blue-500/20 to-purple-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">🎲 Diversity Algorithm</h4>
              <p className="text-white/80 text-sm">MMR (Maximal Marginal Relevance) ensures varied genres, release years, and no sequel overload.</p>
            </div>

            <div className="bg-gradient-to-r from-pink-500/20 to-orange-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">🔄 Auto Background Updates</h4>
              <p className="text-white/80 text-sm">Lists refresh every 24 hours with new releases and updated recommendations.</p>
            </div>

            <div className="bg-gradient-to-r from-green-500/20 to-blue-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">📊 Enhanced Scoring</h4>
              <p className="text-white/80 text-sm">Each recommendation scored on: genre match, mood compatibility, semantic similarity (20k FAISS pool), freshness, networks, creators, directors, and your watch history. Filters applied at SQL level for maximum efficiency.</p>
            </div>

            <div className="bg-gradient-to-r from-indigo-500/20 to-purple-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">🧠 Memory Efficient</h4>
              <p className="text-white/80 text-sm">Smart batching, lazy loading, and automatic garbage collection ensure smooth performance even with 1.4M+ candidates. Handles large AI list generations without memory issues.</p>
            </div>

            <div className="bg-gradient-to-r from-yellow-500/20 to-pink-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">🏷️ Dynamic Titles</h4>
              <p className="text-white/80 text-sm">Streaming service-style personalized titles like "Fans of Inception Also Enjoyed"</p>
            </div>

            <div className="bg-gradient-to-r from-cyan-500/20 to-blue-500/20 backdrop-blur-sm border border-white/20 rounded-xl p-4">
              <h4 className="font-semibold text-white mb-2">🔗 Improved Trakt Sync</h4>
              <p className="text-white/80 text-sm">Automatic TMDB → Trakt ID resolution with caching. All lists sync properly to Trakt, including AI lists with full item counts.</p>
            </div>
          </div>
        </div>
      ),
    },
    {
      id: "faq",
      title: "FAQ",
      icon: "❓",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">Frequently Asked Questions</h3>

          <div className="space-y-4">
            {[
              {
                q: "Why do AI lists only show 1 item on Trakt?",
                a: "This was a bug that has been fixed! The Trakt sync now properly resolves TMDB IDs to Trakt IDs and syncs all items. If you still see issues, try manually re-syncing the list."
              },
              {
                q: "What's new with AI recommendations?",
                a: "AI lists now use a larger 20k candidate pool with smarter SQL-level filtering. We can now match by networks (HBO, Netflix), production countries, show creators, and movie directors - all extracted automatically from your prompt!"
              },
              {
                q: "Why aren't my lists syncing?",
                a: "Check Trakt connection in Settings, verify TMDB API key, try Force Full Sync (Shift + Click), and check browser console for errors."
              },
              {
                q: "How do I get better recommendations?",
                a: "Watch more content (syncs with Trakt automatically), use multiple genres, try different moods, and experiment with obscurity levels."
              },
              {
                q: "Can I export my lists?",
                a: "Yes! All lists automatically sync to Trakt where you can share publicly, export to CSV, or access from any device."
              },
              {
                q: "How often do lists update?",
                a: "Automatic: Every 24 hours. Manual: Click 'Sync' anytime. Watched status updates on every sync."
              },
              {
                q: "Why do I see duplicate titles?",
                a: "Usually different years (remakes/reboots), movies vs TV shows with same name, or regional variants."
              },
              {
                q: "What languages are supported?",
                a: "20+ languages including English, Danish, Swedish, Norwegian, French, German, Spanish, Japanese, Korean, Chinese, and more."
              }
            ].map((faq, idx) => (
              <div key={idx} className="bg-white/5 backdrop-blur-sm rounded-xl p-4">
                <h4 className="font-semibold text-white mb-2">{faq.q}</h4>
                <p className="text-white/80 text-sm">{faq.a}</p>
              </div>
            ))}
          </div>
        </div>
      ),
    },
    {
      id: "troubleshooting",
      title: "Troubleshooting",
      icon: "🔧",
      content: (
        <div className="space-y-4">
          <h3 className="text-2xl font-bold text-white mb-3">Common Issues</h3>

          <div className="space-y-4">
            <div className="bg-white/10 backdrop-blur-sm border border-red-400/30 rounded-xl p-4">
              <h4 className="font-semibold text-red-200 mb-2">Lists stuck on "Syncing..."</h4>
              <ol className="text-white/80 text-sm space-y-1">
                <li>1. Refresh the page</li>
                <li>2. Check Docker containers: <code className="bg-black/30 px-2 py-1 rounded">docker ps</code></li>
                <li>3. View logs: <code className="bg-black/30 px-2 py-1 rounded">docker logs watchbuddy-backend-1</code></li>
              </ol>
            </div>

            <div className="bg-white/10 backdrop-blur-sm border border-yellow-400/30 rounded-xl p-4">
              <h4 className="font-semibold text-yellow-200 mb-2">"No recommendations found"</h4>
              <ul className="text-white/80 text-sm space-y-1">
                <li>• Broaden your filters (fewer genres, wider year range)</li>
                <li>• Disable obscurity filters</li>
                <li>• Try different language/mood combinations</li>
                <li>• Verify TMDB API key is valid</li>
              </ul>
            </div>

            <div className="bg-white/10 backdrop-blur-sm border border-blue-400/30 rounded-xl p-4">
              <h4 className="font-semibold text-blue-200 mb-2">Trakt auth keeps failing</h4>
              <ol className="text-white/80 text-sm space-y-1">
                <li>1. Clear browser cookies</li>
                <li>2. Reauthorize in Settings</li>
                <li>3. Check Trakt status at trakt.tv/vip/status</li>
              </ol>
            </div>

            <div className="bg-white/10 backdrop-blur-sm border border-purple-400/30 rounded-xl p-4">
              <h4 className="font-semibold text-purple-200 mb-2">Container errors</h4>
              <div className="text-white/80 text-sm space-y-2">
                <p>Restart services:</p>
                <code className="block bg-black/30 px-3 py-2 rounded">docker compose restart</code>
                <p className="mt-2">Check logs:</p>
                <code className="block bg-black/30 px-3 py-2 rounded">docker logs watchbuddy-backend-1 --tail 100</code>
                <p className="mt-2">Rebuild if needed:</p>
                <code className="block bg-black/30 px-3 py-2 rounded">docker compose build backend<br/>docker compose up -d backend</code>
              </div>
            </div>
          </div>
        </div>
      ),
    },
  ];

  const activeContent = sections.find(s => s.id === activeSection);

  return (
    <div className="min-h-screen p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <h1 className="text-4xl md:text-5xl font-bold text-white mb-3 flex items-center justify-center gap-3">
            <span className="text-4xl">📖</span> WatchBuddy Help
          </h1>
          <p className="text-white/80 text-lg">Everything you need to know about using WatchBuddy</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Navigation Sidebar */}
          <div className="lg:col-span-1">
            <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-2xl p-4 sticky top-4">
              <h2 className="text-lg font-semibold text-white mb-4">Contents</h2>
              <nav className="space-y-2">
                {sections.map(section => (
                  <button
                    key={section.id}
                    onClick={() => setActiveSection(section.id)}
                    className={`w-full text-left px-4 py-3 rounded-xl transition-all duration-200 flex items-center gap-3 ${
                      activeSection === section.id
                        ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-lg'
                        : 'text-white/80 hover:bg-white/10'
                    }`}
                  >
                    <span className="text-xl">{section.icon}</span>
                    <span className="font-medium">{section.title}</span>
                  </button>
                ))}
              </nav>
            </div>
          </div>

          {/* Content Area */}
          <div className="lg:col-span-3">
            <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-2xl shadow-2xl p-6 md:p-8">
              {activeContent?.content}
            </div>

              <div className="mt-6 text-center">
              <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-4 text-white/60 text-sm">
                <p>Need more help? Check <a href="https://github.com/steffenbb/WatchBuddy" target="_blank" rel="noopener noreferrer" className="text-purple-400 hover:text-purple-300 underline">GitHub</a> or view container logs for debugging.</p>
                <p className="mt-2">Last updated: November 2025 • New: 20k FAISS pool, network/country/creator/director filters, improved Trakt sync</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
