# 🎬 WatchBuddy Help

Welcome to WatchBuddy! Your AI-powered movie and TV show recommendation companion. This guide explains all features and how to get the most out of your experience.

---

## 🚀 Getting Started

### First-Time Setup

1. **Connect Trakt**: Go to Settings → Trakt Authentication → Authorize with Trakt
2. **Add TMDB API Key**: Settings → TMDB API Key (get free key from themoviedb.org)
3. **Create Your First SmartList**: Dashboard → Create SmartList

### Dashboard Overview

- **Lists Section**: All your SmartLists and custom lists
- **Suggested Lists**: Curated recommendations based on your watch history
- **Quick Actions**: Create lists, sync with Trakt, manage settings
- **Status Indicators**: Shows sync status and API health

---

## 📋 SmartLists

SmartLists use AI to recommend content based on your filters and watch history.

### Creating a SmartList

1. Click **"Create SmartList"**
2. Set your filters (see below)
3. Choose item limit (default: 200)
4. Click **"Create"**
5. Click **"Sync"** to populate with recommendations

### Available Filters

#### **Genres** (Multiple selection)
Select one or more genres:
- Action, Adventure, Animation, Comedy, Crime, Documentary
- Drama, Family, Fantasy, Horror, Mystery, Romance
- Sci-Fi, Thriller, War, Western, Music, History

**Match Mode**: 
- **Any**: Item has at least one selected genre
- **All**: Item must have all selected genres

#### **Moods** (Select up to 3)
AI analyzes your watch history to match these emotional tones:
- **Dark**: Gritty, serious, intense themes
- **Cozy**: Feel-good, comforting, warm
- **Tense**: Suspenseful, edge-of-your-seat
- **Quirky**: Unusual, offbeat, eccentric
- **Epic**: Grand scale, sweeping narratives
- **Intimate**: Personal, character-driven stories

#### **Languages**
Filter by original language (20+ supported):
- English (en), Danish (da), Swedish (sv), Norwegian (no)
- French (fr), German (de), Spanish (es), Italian (it)
- Japanese (ja), Korean (ko), Chinese (zh), and more

**Smart Fallback**: If content is scarce, language filter becomes lenient

#### **Year Range**
- **From**: Earliest release year (e.g., 1990)
- **To**: Latest release year (e.g., 2024)
- Leave blank for no restrictions

#### **Obscurity Level**
Controls how popular/obscure your recommendations are:
- **Mainstream**: Popular, widely-known titles
- **Balanced**: Mix of popular and hidden gems
- **Hidden Gems**: Obscure, under-the-radar content
- **Ultra Discovery**: Deep cuts and rare finds

#### **Discovery Mode**
- **Standard**: Best overall recommendations
- **Enhanced**: More varied, exploratory picks
- **Ultra**: Maximum diversity, surprise picks

---

## 🌟 Features

### Dynamic Titles
Netflix-style personalized list titles:
- "Fans of Inception Also Enjoyed"
- "Because You Watched The Matrix"

### Watched Status
- ✓ Green checkmark = You've watched this on Trakt
- Items sync automatically from your Trakt history
- Click item to see watch date

### Diversity Algorithm
No more lists full of sequels! Our MMR (Maximal Marginal Relevance) algorithm ensures:
- Varied genres within your filters
- Different release years
- Mix of obscure and popular titles
- Balanced cast and crew

### Trakt Sync
All lists automatically sync with Trakt:
- Create list in WatchBuddy → appears on Trakt
- Mark watched in Trakt → updates in WatchBuddy
- Delete list in WatchBuddy → removes from Trakt

### Background Updates
Lists refresh automatically every 24 hours to:
- Add newly released content
- Update watched statuses
- Refresh recommendations based on new watch history

---

## 🔧 Managing Lists

### Syncing Lists
Click **"Sync"** on any list to:
- Fetch new recommendations
- Update watched statuses from Trakt
- Apply filter changes

**Force Full Sync**: Hold Shift while clicking "Sync" to completely rebuild the list

### Editing Lists
1. Click list name to view
2. Click **"Edit"** button
3. Modify filters
4. Click **"Save"**
5. Click **"Sync"** to apply changes

### Deleting Lists
1. Go to list view
2. Click **"Delete"** button
3. Confirm deletion

**Note**: This removes the list from both WatchBuddy and Trakt!

### Custom Lists
Create lists with manually selected titles:
1. Click **"Create Custom List"**
2. Search and add titles manually
3. Items sync to Trakt automatically

---

## 📊 Understanding Scores

Each recommendation has a score (0-100%) based on:

1. **Genre Match**: How well it matches your selected genres
2. **Mood Compatibility**: Fits your selected moods
3. **Semantic Similarity**: Similar to movies you've enjoyed
4. **Freshness**: Recently released (if enabled)
5. **Popularity**: Balanced by your obscurity setting
6. **Watch History**: Learns from your Trakt activity

**Higher score = Better match for you!**

---

## ⚙️ Settings

### Trakt Authentication
- **Status**: Shows connection state
- **Reauthorize**: Refresh your Trakt connection
- **Disconnect**: Remove Trakt access (lists remain local)

### TMDB API Key
Required for movie posters and metadata:
1. Get free key at themoviedb.org/settings/api
2. Paste into settings
3. Click "Save"

### Notifications
- **Sync Complete**: When lists finish updating
- **New Suggestions**: When curated lists are available
- **Errors**: When something goes wrong

---

## 💡 Tips & Tricks

### Getting Better Recommendations
- **Use Multiple Genres**: Mix and match for unique results
- **Try Different Moods**: Experiment with combinations
- **Adjust Obscurity**: Find your sweet spot between popular and hidden gems
- **Keep Watching**: The more you watch, the better recommendations get

### Discovering New Content
- Enable **Ultra Discovery Mode** for surprise picks
- Try foreign languages with English subtitles
- Combine unlikely genre pairs (e.g., Comedy + Horror)
- Set wide year ranges to discover classics

### Organizing Your Lists
- Create themed lists (e.g., "Rainy Day Movies", "Friday Night Thrillers")
- Use year ranges for decade-specific lists (e.g., "90s Comedies")
- Separate movie and TV show lists
- Keep item limits reasonable (50-200 for best performance)

---

## ❓ FAQ

### Why aren't my lists syncing?
- Check Trakt connection in Settings
- Verify TMDB API key is valid
- Try "Force Full Sync" (Shift + Click Sync)
- Check browser console for errors

### Where's my watched history?
- Make sure Trakt is connected
- Sync your list to pull watched statuses
- Check that items exist on Trakt (some may be missing)

### Why do I see duplicate titles?
- Different years (remakes/reboots)
- Movies vs TV shows with same name
- Regional variants

### Can I export my lists?
Yes! All lists automatically sync to Trakt, where you can:
- Share publicly
- Export to CSV
- Access from any device

### How often do lists update?
- **Automatic**: Every 24 hours via background tasks
- **Manual**: Click "Sync" anytime
- **Watched Status**: Updates on every sync

### What languages are supported?
20+ languages including:
- English, Danish, Swedish, Norwegian, Finnish
- French, German, Spanish, Italian, Portuguese
- Japanese, Korean, Chinese, Thai, Hindi

---

## 🐛 Troubleshooting

### Lists show "Syncing..." forever
1. Refresh the page
2. Check Docker containers are running: `docker ps`
3. View backend logs: `docker logs watchbuddy-backend-1`

### "No recommendations found"
- Broaden your filters (fewer genres, wider year range)
- Disable obscurity filters
- Try different language/mood combinations
- Check that TMDB API key is valid

### Trakt auth keeps failing
1. Clear browser cookies
2. Reauthorize in Settings
3. Check Trakt isn't down (trakt.tv/vip/status)

### Container errors
```bash
# Restart services
docker compose restart

# Check logs
docker logs watchbuddy-backend-1 --tail 100

# Rebuild if needed
docker compose build backend
docker compose up -d backend
```

---

## 📞 Need More Help?

- **GitHub Issues**: Report bugs or request features
- **Logs**: Check `docker logs watchbuddy-backend-1` for errors
- **Docker Health**: Run `docker compose ps` to check services

---

_Happy Watching! 🍿_

---

## 🆕 What’s New (Oct 2025)

- Persistent-only list syncs: Syncs now read exclusively from the internal database for speed and reliability.
- Background ingestion workers: Run every 2 hours (~12 minutes) using TMDB Search (multi) to discover new titles; resolves Trakt IDs by TMDB ID when possible.
- System Health widget: Shows Movie/TV ingestion worker status (Running/Completed/Error), last/next run times, and items processed.
- Trakt list creation:
	- Custom, Suggested, and Smart lists create a corresponding Trakt list at creation time (if authenticated)
	- Initial items are pushed to Trakt during the first population task (within seconds of creation)

If your Trakt list is missing or empty after creation:
- Check Settings → Trakt, ensure you’re authenticated
- Check System Health → Trakt API “Online”
- Wait 10–60 seconds for initial population to push items; then refresh
- Use “Sync” on the list to retry
