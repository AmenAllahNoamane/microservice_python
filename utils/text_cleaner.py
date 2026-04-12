import re 

# Nettoie le texte extrait
def clean_text(text):
    
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\s+([,.;:])', r'\1', text)
    text = re.sub(r'([,.;:])\s+', r'\1 ', text)

    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if len(re.findall(r'\w', line)) >= 1:
            lines.append(line)

    return '\n'.join(lines).strip()

# Normalise les dates au format JJ/MM/AAAA
def fix_dates(text):
    
    date_pattern = r'(\d{1,2})\s*[\-\.\/]+\s*[\-\.\/]*\s*(\d{1,2})\s*[\-\.\/]+\s*(\d{4})'

    def replace_date(match):
        day = match.group(1).zfill(2)
        month = match.group(2).zfill(2)
        year = match.group(3)
        return f"{day}/{month}/{year}"

    return re.sub(date_pattern, replace_date, text)
