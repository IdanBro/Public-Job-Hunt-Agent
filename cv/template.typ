// ATS-friendly single-column CV. Rendered by `python -m jobagent render`, which passes
// the CV YAML path (relative to the repo root) as sys.inputs.cv.
//
// Text fields (summary, bullets) are Typst markup: *bold*, #link("url")[text].
// Escape these characters with a backslash when they are meant literally: # $ @ < * _ `

#let cv = yaml(sys.inputs.at("cv"))
#let accent = rgb("#1f4e79")
#let muted = luma(85)
#let md(s) = eval(s, mode: "markup")

#set document(title: cv.name + " - " + cv.headline, author: cv.name)
#set page(paper: "a4", margin: (x: 1.4cm, y: 0.8cm))
// Liberation Sans is bundled in cv/fonts (metric-compatible with Arial), so PDFs look the same on every machine.
#set text(font: ("Liberation Sans", "Arial"), size: 9.4pt, lang: "en")
#set par(leading: 0.48em, spacing: 0.6em)
#set list(marker: text(fill: accent)[•], indent: 0.2em, body-indent: 0.45em, spacing: 0.42em)
#show link: set text(fill: accent)

#let section(title) = block(
  above: 0.85em,
  below: 0.55em,
  width: 100%,
  inset: (bottom: 2.5pt),
  stroke: (bottom: 0.6pt + accent),
  text(fill: accent, weight: "bold", size: 11.5pt, upper(title)),
)

#let c = cv.contact
#let sep = [#h(0.35em)|#h(0.35em)]
#align(center)[
  #block(below: 0.45em, text(size: 22pt, weight: "bold", upper(cv.name)))
  #block(below: 0.45em, text(size: 13pt, weight: "bold", fill: accent, upper(cv.headline)))
  #text(size: 8.6pt, fill: muted)[
    #c.location #sep #c.phone #sep
    #link("mailto:" + c.email, c.email) #sep
    #link("https://" + c.linkedin, c.linkedin) #sep
    #link("https://" + c.github, c.github)
  ]
]

#section("Professional Summary")
#md(cv.summary)

#section("Professional Experience")
#for job in cv.experience [
  #block(breakable: false, above: 0.6em, below: 0.6em)[
    #grid(
      columns: (1fr, auto),
      row-gutter: 0.4em,
      text(weight: "bold", size: 10.4pt, job.title), text(weight: "bold", size: 9.6pt, job.dates),
      // An entry with no employer (a gap, or independent projects) has only a title and dates.
      ..if "org" in job { (text(style: "italic", fill: muted, job.org), []) },
    )
    #if "bullets" in job [
      #v(0.1em)
      #list(..job.bullets.map(md))
    ]
  ]
]

#section("Technical Skills")
#for group in cv.skills [
  *#group.category:* #group.items.join(", ") \
]

#section("Education")
#for item in cv.education [
  *#item.title* #if "detail" in item [#h(0.3em)|#h(0.3em) #text(fill: muted, item.detail)] \
]
