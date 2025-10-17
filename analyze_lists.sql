-- Comprehensive List Analysis Query
-- This analyzes each list for: uniqueness, media type compliance, and content relevance

-- 1. Get all lists with their filters and item counts
SELECT 
    ul.id,
    ul.title,
    ul.list_type,
    ul.filters::text as filters_json,
    COUNT(li.id) as item_count,
    COUNT(DISTINCT li.trakt_id) as unique_items
FROM user_lists ul
LEFT JOIN list_items li ON ul.id = li.smartlist_id
WHERE ul.user_id = 1
GROUP BY ul.id, ul.title, ul.list_type, ul.filters
ORDER BY ul.id;

-- 2. Media type distribution per list
SELECT 
    smartlist_id,
    media_type,
    COUNT(*) as count
FROM list_items
WHERE smartlist_id IN (SELECT id FROM user_lists WHERE user_id = 1)
GROUP BY smartlist_id, media_type
ORDER BY smartlist_id, media_type;

-- 3. Check for duplicate items across lists
SELECT 
    li1.trakt_id,
    li1.title,
    li1.media_type,
    string_agg(DISTINCT li1.smartlist_id::text, ', ' ORDER BY li1.smartlist_id::text) as appears_in_lists,
    COUNT(DISTINCT li1.smartlist_id) as list_count
FROM list_items li1
WHERE li1.smartlist_id IN (SELECT id FROM user_lists WHERE user_id = 1)
GROUP BY li1.trakt_id, li1.title, li1.media_type
HAVING COUNT(DISTINCT li1.smartlist_id) > 1
ORDER BY list_count DESC, li1.title
LIMIT 50;

-- 4. Sample items from each list (first 10 per list)
SELECT 
    li.smartlist_id,
    ul.title as list_title,
    li.title as item_title,
    li.media_type,
    li.trakt_id,
    li.score
FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY smartlist_id ORDER BY score DESC) as rn
    FROM list_items
    WHERE smartlist_id IN (SELECT id FROM user_lists WHERE user_id = 1)
) li
JOIN user_lists ul ON li.smartlist_id = ul.id
WHERE li.rn <= 10
ORDER BY li.smartlist_id, li.rn;
