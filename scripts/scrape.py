#!/usr/bin/env python3
"""
Truth Anchor — RSS scraper
Pulls latest articles from all sources, clusters by topic, generates static site
"""

import json
import os
import re
import html
import time
import feedparser
from datetime import datetime, timezone
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
SITE_DIR = os.path.join(BASE_DIR, 'site')

def load_sources():
    with open(os.path.join(DATA_DIR, 'sources.json')) as f:
        return json.load(f)['sources']

def extract_real_url(link):
    """Extract real article URL from Google News redirect links."""
    if 'news.google.com/rss/articles' in link:
        # Google News uses redirect URLs — we can't easily extract the real URL
        # but the source element in the feed tells us the domain
        return link
    return link

def clean_google_news_title(title, source_domain):
    """Google News titles end with ' - Source Name'. Remove that suffix."""
    if source_domain and 'news.google.com' not in title:
        # Remove trailing ' - SourceName' pattern
        cleaned = re.sub(r'\s*[-–—]\s*[A-Za-z0-9\s.]+$', '', title).strip()
        if cleaned:
            return cleaned
    return title

def fetch_rss(source):
    """Fetch and parse an RSS feed. Returns list of articles."""
    articles = []
    is_google_news = 'news.google.com/rss' in source.get('rss', '')
    try:
        feed = feedparser.parse(source['rss'])
        for entry in feed.entries[:12]:  # Top 12 per source
            title = html.unescape(entry.get('title', ''))
            link = entry.get('link', '')

            # For Google News RSS, clean the title (it has '- Source' suffix)
            if is_google_news:
                title = clean_google_news_title(title, source['domain'])

            published = entry.get('published', '')
            summary = html.unescape(entry.get('summary', entry.get('description', '')))
            # Clean up HTML tags from summary
            summary = re.sub(r'<[^>]+>', '', summary)[:300]

            # Skip title-only feeds that don't have enough info
            if not title or len(title) < 15:
                continue

            articles.append({
                'title': title,
                'url': link,
                'published': published,
                'summary': summary,
                'source': source['name'],
                'domain': source['domain'],
                'bias': source['bias'],
                'color': source['color'],
                'owner': source['owner'],
            })
    except Exception as e:
        print(f"  Error fetching {source['name']}: {e}")
    return articles

def extract_keywords(text):
    """Extract meaningful keywords from text for clustering."""
    text = text.lower()
    stopwords = {'a','an','the','and','or','but','in','on','at','to','for',
                 'of','with','by','from','up','about','into','over','after',
                 'is','are','was','were','be','been','being','have','has',
                 'had','do','does','did','will','would','could','should',
                 'may','might','shall','it','its','that','this','these',
                 'those','i','you','he','she','we','they','them','their',
                 'what','which','who','whom','when','where','why','how',
                 'all','each','every','both','few','more','most','some',
                 'no','not','only','own','same','so','than','too','very',
                 'just','also','new','says','said','after','report','says'}
    words = re.findall(r'\b[a-z]{4,}\b', text)
    return set(w for w in words if w not in stopwords)

def dedupe_by_source(articles):
    """Keep only the most recent article per unique source."""
    seen = {}
    deduped = []
    for a in articles:
        src = a.get('source', '').lower()
        if src not in seen or a.get('published', '') > seen[src].get('published', ''):
            if src in seen:
                # Remove old one
                deduped = [x for x in deduped if x.get('source', '').lower() != src]
            seen[src] = a
            deduped.append(a)
    return deduped

def cluster_articles(all_articles):
    """Cluster articles by topic using keyword overlap."""
    if not all_articles:
        return []

    # Step 1: Extract keywords for all articles (keep all, dedupe per cluster later)
    for article in all_articles:
        article['_keywords'] = extract_keywords(article['title'] + ' ' + article.get('summary', ''))

    # Step 2: Build similarity graph
    articles = all_articles
    n = len(articles)
    adjacency = {i: set() for i in range(n)}

    for i in range(n):
        for j in range(i + 1, n):
            overlap = articles[i]['_keywords'] & articles[j]['_keywords']
            if len(overlap) >= 3:
                adjacency[i].add(j)
                adjacency[j].add(i)

    # Step 3: Find connected components (topic clusters)
    visited = set()
    clusters = []
    for i in range(n):
        if i in visited:
            continue
        component = []
        stack = [i]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            stack.extend(adjacency[node] - visited)
        if len(component) >= 2:
            cluster = [articles[idx] for idx in component]
            clusters.append(cluster)

    # Step 4: Dedupe within each cluster (one article per source per cluster)
    clusters = [dedupe_by_source(c) for c in clusters]
    # Remove clusters that dropped below 2 sources after dedupe
    clusters = [c for c in clusters if len(c) >= 2]

    # Step 5: Merge clusters that are about the same topic
    merged = []
    used_clusters = set()
    for i, c1 in enumerate(clusters):
        if i in used_clusters:
            continue
        merged_cluster = list(c1)
        used_clusters.add(i)
        for j, c2 in enumerate(clusters):
            if j in used_clusters or i == j:
                continue
            merge_this = False
            for a1 in c1:
                for a2 in c2:
                    overlap = a1['_keywords'] & a2['_keywords']
                    if len(overlap) >= 3:
                        merge_this = True
                        break
                if merge_this:
                    break
            if merge_this:
                merged_cluster.extend(c2)
                used_clusters.add(j)
        # Rededupe the merged cluster by source
        merged_cluster = dedupe_by_source(merged_cluster)
        if len(merged_cluster) >= 2:
            merged.append(merged_cluster)

    # Sort by size (bigger stories first)
    merged.sort(key=lambda c: len(c), reverse=True)
    return merged[:15]

def generate_html(clusters, sources_count, timestamp):
    """Generate the static site HTML."""
    bias_counts = {}
    for cluster in clusters:
        for article in cluster:
            bias = article['bias']
            bias_counts[bias] = bias_counts.get(bias, 0) + 1

    total_articles = sum(len(c) for c in clusters)

    # Bias legend
    bias_labels = {
        'left': 'Left',
        'left-center': 'Left-Center',
        'center': 'Center',
        'center-right': 'Center-Right',
        'right': 'Right',
    }
    bias_colors = {
        'left': '#1a237e',
        'left-center': '#3949ab',
        'center': '#888888',
        'center-right': '#cc3333',
        'right': '#cc2222',
    }

    # Build story cards
    stories_html = ''
    for idx, cluster in enumerate(clusters):
        # Pick best title (prefer center sources, then the shortest most descriptive)
        titles = [a['title'] for a in cluster]
        # Prefer center + AP/Reuters titles as most factual
        center_titles = [a['title'] for a in cluster if a['bias'] == 'center']
        best_title = center_titles[0] if center_titles else titles[0]

        # Build source bars
        bias_distribution = {}
        for a in cluster:
            b = a['bias']
            if b not in bias_distribution:
                bias_distribution[b] = {'count': 0, 'sources': []}
            bias_distribution[b]['count'] += 1
            bias_distribution[b]['sources'].append(a)

        bar_chart = '<div class="bias-bar">'
        for bias_key in ['left', 'left-center', 'center', 'center-right', 'right']:
            if bias_key in bias_distribution:
                pct = bias_distribution[bias_key]['count'] / len(cluster) * 100
                pct = max(pct, 5)
                bar_chart += f'<div class="bar-segment" style="width:{pct}%;background:{bias_colors[bias_key]}">'
                bar_chart += f'<span class="bar-label">{bias_distribution[bias_key]["count"]}</span></div>'
            else:
                bar_chart += f'<div class="bar-segment bar-empty" style="width:5%"></div>'
        bar_chart += '</div>'

        # Determine coverage gaps
        biases_present = set(a['bias'] for a in cluster)
        missing = []
        for b in ['left', 'left-center', 'center', 'center-right', 'right']:
            if b not in biases_present:
                missing.append(b)
        gap_note = ''
        if missing:
            labels = [bias_labels[m] for m in missing]
            gap_note = f'<div class="gap-note">⚠ No coverage from: {", ".join(labels)}</div>'

        # Article cards
        articles_list = ''
        for a in cluster:
            bias_label = bias_labels.get(a['bias'], a['bias'])
            articles_list += f'''
            <a href="{a['url']}" target="_blank" rel="noopener" class="article-link">
              <div class="article-card" style="border-left: 4px solid {a['color']}">
                <div class="article-source">
                  <span class="bias-tag" style="background:{bias_colors.get(a['bias'],'#888')}">{bias_label}</span>
                  <span class="source-name">{a['source']}</span>
                  <span class="owner-name">{a['owner'].split('(')[0].strip()}</span>
                </div>
                <div class="article-title">{html.escape(a['title'])}</div>
              </div>
            </a>'''

        stories_html += f'''
        <div class="story-cluster" id="story-{idx}">
          <div class="story-header">
            <h2 class="story-title">{html.escape(best_title)}</h2>
            <div class="coverage-meta">{len(cluster)} sources · {len(biases_present)}/5 of spectrum · {gap_note}</div>
            {bar_chart}
          </div>
          <div class="articles-grid">
            {articles_list}
          </div>
        </div>
        '''

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Truth Anchor — See Every Side</title>
  <meta name="description" content="See the same news story reported across the political spectrum. Left, center, right — side by side. You decide.">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f0f0f;
      color: #e0e0e0;
      line-height: 1.6;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
    header {{
      text-align: center;
      padding: 40px 20px 30px;
      border-bottom: 1px solid #2a2a2a;
      margin-bottom: 30px;
    }}
    header h1 {{
      font-size: 2.5rem;
      font-weight: 700;
      letter-spacing: -1px;
    }}
    header h1 span.anchor {{ color: #cc2222; }}
    header h1 span.truth {{ color: #1a237e; }}
    header p.subtitle {{
      color: #888;
      font-size: 1.05rem;
      margin-top: 8px;
    }}
    header p.subtitle em {{ color: #aaa; font-style: italic; }}
    .status-bar {{
      display: flex;
      justify-content: center;
      gap: 30px;
      margin-top: 15px;
      font-size: 0.85rem;
      color: #666;
    }}
    .status-bar span {{ background: #1a1a1a; padding: 4px 12px; border-radius: 12px; }}

    /* Story Cluster */
    .story-cluster {{
      background: #1a1a1a;
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
      border: 1px solid #2a2a2a;
    }}
    .story-header h2 {{
      font-size: 1.3rem;
      font-weight: 600;
      color: #ddd;
      margin-bottom: 8px;
    }}
    .coverage-meta {{
      font-size: 0.85rem;
      color: #888;
      margin-bottom: 12px;
    }}
    .gap-note {{
      display: inline;
      color: #bb8844;
      font-size: 0.85rem;
    }}

    /* Bias Bar */
    .bias-bar {{
      display: flex;
      height: 24px;
      border-radius: 12px;
      overflow: hidden;
      margin-bottom: 16px;
    }}
    .bar-segment {{
      display: flex;
      align-items: center;
      justify-content: center;
      transition: width 0.3s;
      min-width: 20px;
    }}
    .bar-empty {{ background: transparent; min-width: 5px; }}
    .bar-label {{
      font-size: 0.7rem;
      font-weight: 700;
      color: #fff;
      text-shadow: 0 1px 2px rgba(0,0,0,0.5);
    }}

    /* Articles Grid */
    .articles-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 12px;
    }}
    .article-link {{ text-decoration: none; color: inherit; }}
    .article-card {{
      background: #232323;
      border-radius: 8px;
      padding: 14px;
      transition: transform 0.15s, box-shadow 0.15s;
    }}
    .article-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      background: #2a2a2a;
    }}
    .article-source {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      font-size: 0.8rem;
    }}
    .bias-tag {{
      font-size: 0.65rem;
      font-weight: 600;
      padding: 2px 6px;
      border-radius: 4px;
      color: #fff;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .source-name {{
      font-weight: 600;
      color: #ccc;
    }}
    .owner-name {{
      color: #666;
      font-size: 0.7rem;
    }}
    .article-title {{
      font-size: 0.9rem;
      color: #ccc;
      line-height: 1.4;
    }}

    /* Legend */
    .legend {{
      display: flex;
      justify-content: center;
      gap: 16px;
      flex-wrap: wrap;
      margin-bottom: 30px;
      padding: 16px;
      background: #1a1a1a;
      border-radius: 8px;
      border: 1px solid #2a2a2a;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.8rem;
      color: #999;
    }}
    .legend-dot {{
      width: 12px;
      height: 12px;
      border-radius: 3px;
    }}

    /* Footer */
    footer {{
      text-align: center;
      padding: 40px 20px;
      color: #555;
      font-size: 0.8rem;
      border-top: 1px solid #2a2a2a;
      margin-top: 40px;
    }}
    footer a {{ color: #6699cc; text-decoration: none; }}
    footer a:hover {{ text-decoration: underline; }}

    /* Mobile */
    @media (max-width: 600px) {{
      header h1 {{ font-size: 1.8rem; }}
      .articles-grid {{ grid-template-columns: 1fr; }}
      .status-bar {{ flex-direction: column; align-items: center; gap: 8px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1><span class="truth">Truth</span> <span class="anchor">Anchor</span></h1>
      <p class="subtitle">The same news story. Across the spectrum. <em>You decide.</em></p>
      <div class="status-bar">
        <span>{len(clusters)} stories tracked</span>
        <span>{total_articles} articles</span>
        <span>{sources_count} sources monitored</span>
        <span>Updated {timestamp}</span>
      </div>
    </header>

    <div class="legend">
      <div class="legend-item"><div class="legend-dot" style="background:#1a237e"></div> Left / Dem</div>
      <div class="legend-item"><div class="legend-dot" style="background:#3949ab"></div> Left-Center</div>
      <div class="legend-item"><div class="legend-dot" style="background:#888888"></div> Center</div>
      <div class="legend-item"><div class="legend-dot" style="background:#cc3333"></div> Center-Right</div>
      <div class="legend-item"><div class="legend-dot" style="background:#cc2222"></div> Right / GOP</div>
    </div>

    <main>
      {stories_html if stories_html else '<p style="text-align:center;color:#666;padding:60px 0;">No stories matched across multiple sources this cycle. Check back soon.</p>'}
    </main>

    <footer>
      <p>Truth Anchor is a non-partisan project. We do not tell you what to think.</p>
      <p>We show you who is reporting what, and let you judge the spin.</p>
      <p><a href="https://github.com/retiredtrucker/truth-anchor">Source</a> · Data from RSS feeds · Bias labels from <a href="https://mediabiasfactcheck.com">MediaBiasFactCheck</a></p>
    </footer>
  </div>
</body>
</html>'''
    return html_content


def write_article_data(clusters):
    """Write raw article data as JSON for potential API use."""
    output = []
    for cluster in clusters:
        output.append([{
            'title': a['title'],
            'url': a['url'],
            'source': a['source'],
            'bias': a['bias'],
            'domain': a['domain'],
        } for a in cluster])
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, 'latest.json'), 'w') as f:
        json.dump(output, f, indent=2)


def main():
    print("Truth Anchor — Starting scrape cycle")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print()

    sources = load_sources()
    print(f"Loaded {len(sources)} sources")

    # Fetch all articles
    all_articles = []
    for source in sources:
        print(f"Fetching {source['name']}...")
        articles = fetch_rss(source)
        print(f"  Got {len(articles)} articles")
        all_articles.extend(articles)
        time.sleep(1)  # Be nice to RSS servers

    print(f"\nTotal articles collected: {len(all_articles)}")

    # Cluster by topic
    clusters = cluster_articles(all_articles)
    print(f"Found {len(clusters)} story clusters")

    # Generate site
    timestamp = datetime.now(timezone.utc).strftime('%B %d, %Y at %H:%M UTC')
    html = generate_html(clusters, len(sources), timestamp)

    # Write files
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, 'index.html'), 'w') as f:
        f.write(html)
    write_article_data(clusters)

    print(f"\nSite written to {SITE_DIR}/index.html")
    print("Done.")


if __name__ == '__main__':
    main()
