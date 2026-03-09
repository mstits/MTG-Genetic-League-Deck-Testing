import os
import glob

html_files = glob.glob('web/templates/*.html')

header = """
<!-- Persistent Navigation Header -->
<nav class="bg-gray-900 border-b border-gray-800 sticky top-0 z-50 shadow-md">
    <div class="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
        <div class="flex items-center gap-6">
            <a href="/" class="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400 font-extrabold text-lg tracking-tight">🧬 MTG Genetic League</a>
            <div class="hidden md:flex gap-4">
                <a href="/" class="text-sm font-medium text-gray-300 hover:text-white transition-colors">Dashboard</a>
                <a href="/matches" class="text-sm font-medium text-gray-300 hover:text-white transition-colors">Matches</a>
                <a href="/admin/butterfly" class="text-sm font-medium text-gray-300 hover:text-white transition-colors">Admin Portal</a>
            </div>
        </div>
        <div class="flex items-center gap-3">
            <div class="w-2.5 h-2.5 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_rgba(34,197,94,0.6)]"></div>
            <span class="text-xs font-mono text-green-400">System Online</span>
        </div>
    </div>
</nav>
"""

footer = """
<!-- Dev Footer -->
<footer class="bg-gray-900 border-t border-gray-800 py-6 mt-12 w-full">
    <div class="max-w-7xl mx-auto px-6 flex justify-between items-center text-xs text-gray-500">
        <div>MTG Simulator Alpha v0.9 • 2026 Engine Rules 100% Core</div>
        <div class="flex gap-4">
            <a href="/admin/butterfly" class="hover:text-indigo-400 transition-colors flex items-center gap-1">
                ⚙️ Admin Portal
            </a>
            <a href="https://github.com/MTGGeneticLeague" class="hover:text-white transition-colors" target="_blank">GitHub Repo</a>
        </div>
    </div>
</footer>
"""

for file_path in html_files:
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Don't inject twice
    if "<!-- Persistent Navigation Header -->" in content:
        continue
        
    # Inject header right after <body...>
    import re
    body_tag_match = re.search(r'<body[^>]*>', content)
    if body_tag_match:
        body_end = body_tag_match.end()
        content = content[:body_end] + "\n" + header + content[body_end:]
        
    # Inject footer right before </body>
    if "</body>" in content:
        content = content.replace("</body>", footer + "\n</body>")
        
    with open(file_path, 'w') as f:
        f.write(content)
        
print(f"Updated {len(html_files)} templates.")
