"""Source-type classification.

Every cited domain gets one label. This is the axis that turns "here are 469
domains" into "models learn about this category from review aggregators, not
from brand websites" — which is the finding a marketing leader can act on.

Labels
------
owned              The brand's own domain (resolved at parse time, not here)
review_aggregator  Sites whose product is rankings/reviews of the category
community          User-generated discussion: Reddit, Quora, HN, Discord
editorial          News, magazines, independent blogs with editorial staff
video              Video platforms
educational        .edu institutions and public education bodies
government         .gov and official labour/statistics bodies
competitor_content Another vendor in the category publishing about the category
content_farm       Thin/SEO-built sites that exist to rank, not to inform
other              Unclassified. Keep this under ~15% or keep classifying.

"content_farm" is a judgement call and the study must say so in its limitations
section. The rule used here: no named editorial staff, no masthead, page exists
mainly to host an affiliate ranking list. Spot-check before publishing any
claim that leans on this label.
"""
from __future__ import annotations

# Exact-domain rules. Matching also covers subdomains via suffix logic below.
DOMAIN_TYPES: dict[str, str] = {
    # --- review aggregators / ranking sites -------------------------------
    # hakia.com: NOT a content farm. Formerly a semantic search engine
    # (2000s), the domain now runs a degree/bootcamp program-matching search
    # tool. Functionally a third-party discovery surface like Course Report,
    # so it sits here — but it is a directory, not a review site. Verified by
    # hand 2026-09-30.
    "hakia.com": "review_aggregator",
    "coursereport.com": "review_aggregator",
    "careerkarma.com": "review_aggregator",
    "switchup.org": "review_aggregator",
    "bootcamprankings.com": "review_aggregator",
    "g2.com": "review_aggregator",
    "trustpilot.com": "review_aggregator",
    "coursereport.co": "review_aggregator",
    "bestcolleges.com": "review_aggregator",
    "research.com": "review_aggregator",
    "coursesity.com": "review_aggregator",
    "classcentral.com": "review_aggregator",
    "reviews.io": "review_aggregator",

    # --- community ---------------------------------------------------------
    "reddit.com": "community",
    "quora.com": "community",
    "news.ycombinator.com": "community",
    "ycombinator.com": "community",
    "stackoverflow.com": "community",
    "stackexchange.com": "community",
    "discord.com": "community",
    "x.com": "community",
    "twitter.com": "community",
    "linkedin.com": "community",
    "facebook.com": "community",
    "glassdoor.com": "community",
    "indeed.com": "community",
    "trustradius.com": "community",

    # --- editorial ---------------------------------------------------------
    "forbes.com": "editorial",
    "fortune.com": "editorial",
    "techcrunch.com": "editorial",
    "wsj.com": "editorial",
    "nytimes.com": "editorial",
    "businessinsider.com": "editorial",
    "cnbc.com": "editorial",
    "theverge.com": "editorial",
    "wired.com": "editorial",
    "medium.com": "editorial",
    "substack.com": "editorial",
    "washingtonpost.com": "editorial",
    "npr.org": "editorial",
    "theguardian.com": "editorial",
    "inc.com": "editorial",
    "fastcompany.com": "editorial",
    "usnews.com": "editorial",
    "computerscience.org": "editorial",
    "builtin.com": "editorial",
    "hackernoon.com": "editorial",
    "dev.to": "editorial",
    "freecodecamp.org": "editorial",

    # --- video -------------------------------------------------------------
    "youtube.com": "video",
    "youtu.be": "video",
    "vimeo.com": "video",
    "tiktok.com": "video",

    # --- government / official statistics ----------------------------------
    "bls.gov": "government",
    "ed.gov": "government",
    "sec.gov": "government",
    "ftc.gov": "government",
    "collegescorecard.ed.gov": "government",

    # --- competitor / vendor content ---------------------------------------
    # Other training vendors in the category publishing about the category.
    "nucamp.co": "competitor_content",
    "metana.io": "competitor_content",
    "scrimba.com": "competitor_content",
    "boot.dev": "competitor_content",
    "datacamp.com": "competitor_content",
    "dataquest.io": "competitor_content",
    "codecademy.com": "competitor_content",
    "udacity.com": "competitor_content",
    "coursera.org": "competitor_content",
    "udemy.com": "competitor_content",
    "edx.org": "competitor_content",
    "pluralsight.com": "competitor_content",
    "lewagon.com": "competitor_content",
    "ironhack.com": "competitor_content",
    "brainstation.io": "competitor_content",
    "turing.com": "competitor_content",

    # --- suspected content farms -------------------------------------------
    # Flagged from the existing corpus. VERIFY BY HAND before publishing.
    "computersciencehero.com": "content_farm",
    "onlinedegreehero.com": "content_farm",
    "jobtraininghub.com": "content_farm",
    "radcity.net": "content_farm",
    "thisisanitsupportgroup.com": "content_farm",
    "edfoundationsrq.org": "content_farm",
    "academi.dev": "content_farm",
    "careercenters.com": "content_farm",

    # --- second pass: classified from the existing corpus ------------------
    # Added after re-parsing 1,022 answers, where "other" was 33% of all
    # citations. Every domain below was hand-checked against the rule stated
    # at the top of this file.
    "en.wikipedia.org": "editorial",
    "wikipedia.org": "editorial",
    "prnewswire.com": "editorial",
    "businesswire.com": "editorial",
    "analyticsinsight.net": "editorial",
    "plainenglish.io": "editorial",
    "techinterview.org": "editorial",
    "blacksintechnology.net": "community",
    "internationalsnetwork.org": "community",
    "codelabsacademy.com": "competitor_content",
    "uxcel.com": "competitor_content",
    "climbhire.co": "competitor_content",
    "generalassemb.ly": "competitor_content",
    "wework.com": "competitor_content",
    "climbcredit.com": "competitor_content",
    "newapprenticeship.com": "competitor_content",
    "studydatascience.org": "content_farm",
    "digitaldefynd.com": "content_farm",
    "dailyvanguard.com": "content_farm",
    "bestjobsearchapps.com": "content_farm",
    "bestcodingbootcamps.tech": "content_farm",
    "best-coding-bootcamps-reviews.com": "content_farm",
    "skillifysolutions.com": "content_farm",
    "savingsgrove.com": "content_farm",
    "buildyouraicareer.com": "content_farm",
    "gloobia.com": "content_farm",
    "standout.work": "content_farm",

    # --- third pass: classified from the 4-engine study corpus -------------
    # The study surfaced 356 domains the monitor never saw, 40% of all
    # citations. Most are bootcamp vendors the brand list does not track.

    # Bootcamp and training vendors not in the tracked brand list. These are
    # competitors publishing about the category, same as the tracked ones.
    "techelevator.com": "competitor_content",
    "codingtemple.com": "competitor_content",
    "codeplatoon.org": "competitor_content",
    "adadevelopersacademy.org": "competitor_content",
    "codesmith.io": "competitor_content",
    "codesmithdocs.s3.us-west-1.amazonaws.com": "competitor_content",
    "careerfoundry.com": "competitor_content",
    "launchschool.com": "competitor_content",
    "perscholas.org": "competitor_content",
    "wbscodingschool.com": "competitor_content",
    "bayvalleytech.com": "competitor_content",
    "archisacademy.com": "competitor_content",
    "algocademy.com": "competitor_content",
    "coddy.tech": "competitor_content",
    "educative.io": "competitor_content",
    "theforage.com": "competitor_content",
    "prentus.com": "competitor_content",
    "mentorcruise.com": "competitor_content",
    "lemon.io": "competitor_content",

    # Directories and ranking sites.
    "bootcamps.org": "review_aggregator",
    "collegeconsensus.com": "review_aggregator",
    "mastersindatascience.org": "review_aggregator",
    "classesplace.com": "review_aggregator",
    "onlinecourseing.com": "review_aggregator",

    # Student lending — a commercial service, not content.
    "ascentfunding.com": "commercial_service",

    # --- fourth pass: verified by hand, 2026-10-03 -------------------------
    # Every domain below was opened and read. Note the direction of travel:
    # manual checks have REMOVED domains from content_farm three times now
    # (hakia, assignmentdude, rockstardeveloperuniversity) and added none.
    # Treat the automated content-farm guess as a hypothesis generator, never
    # as a result.
    "rockstardeveloperuniversity.com": "competitor_content",   # dev academy
    "collegevaluesonline.com": "review_aggregator",            # rankings from
    # IPEDS, College Scorecard and BLS — federal outcome data, not a farm.
    "commandlinux.com": "editorial",                           # Linux docs
    # and tutorials; a real technical publication.

    # Real commercial products, unrelated to this category, cited via their
    # blogs. Not farms: the products exist and work. But an AI voice changer
    # and a code-language detector are being used as sources about choosing a
    # coding bootcamp, which is its own observation about what engines will
    # accept as relevant.
    "voxbooster.com": "offtopic_commercial",        # AI voice changer
    "aicodedetector.com": "offtopic_commercial",    # code language detector

    # Independent outcomes-reporting standards body. Worth its own label:
    # engines citing the category's own audit body is a finding, and burying
    # it in "editorial" would hide it.
    "cirr.org": "industry_body",

    "grsee.com": "content_farm",
}

# Commercial services whose product is not content at all. Small in volume,
# but worth isolating: an academic ghostwriting service being cited as a
# source about education is a finding in its own right, and burying it inside
# "content farm" would hide it.
COMMERCIAL_SERVICES = {
    "assignmentdude.com": "commercial_service",   # paid homework/essay service
}
DOMAIN_TYPES.update(COMMERCIAL_SERVICES)

# UNVERIFIED — check these by hand before publishing anything that leans on
# the content-farm number. They have the shape of thin affiliate sites, but
# shape is not evidence. They stay "other" until a human opens them, which
# keeps the content_farm figure honest rather than flattering.
#
#   rockstardeveloperuniversity.com   45 citations — 2nd most cited unknown
#   whatisthesalary.com               6
#   thecampusreview.com               6
#   collegevaluesonline.com           8
#   edubracket.com                    6
#   degreecalc.com                    6
#   executivelevels.com               6
#   scholarshipsandgrants.us          6
#   commandlinux.com                  7
#   voxbooster.com                    9
#   mctaba.com                        6
#   citehawk.com                      6
#   aicodedetector.com                8
#   blog.theinterviewguys.com         7
NEEDS_REVIEW = [
    "rockstardeveloperuniversity.com", "whatisthesalary.com",
    "thecampusreview.com", "collegevaluesonline.com", "edubracket.com",
    "degreecalc.com", "executivelevels.com", "scholarshipsandgrants.us",
    "commandlinux.com", "voxbooster.com", "mctaba.com", "citehawk.com",
    "aicodedetector.com", "blog.theinterviewguys.com",
]

# Suffix rules applied when no exact match is found. Order matters.
SUFFIX_TYPES: list[tuple[str, str]] = [
    (".edu", "educational"),
    (".ac.uk", "educational"),
    (".gov", "government"),
    (".mil", "government"),
]

# Infrastructure domains that are not really "sources" — CDN and asset hosts.
# Counting these as citations inflates totals and means nothing to a reader.
INFRA_DOMAINS: set[str] = {
    "assets.ctfassets.net",
    "images.ctfassets.net",
    "cdn.prod.website-files.com",
    "lh3.googleusercontent.com",
    "vertexaisearch.cloud.google.com",
    "googleusercontent.com",
    "s3.amazonaws.com",
    "cloudfront.net",
}


def domain_matches(domain: str, target: str) -> bool:
    """True if `domain` is `target` or a subdomain of it.

    This is the fix for the bug that was silently undercounting owned
    citations: work-study.flatironschool.com never equalled flatironschool.com
    under the old exact-match test, so 71 citations were scored as zero.
    """
    d = (domain or "").lower().lstrip(".")
    t = (target or "").lower().lstrip(".")
    if not d or not t:
        return False
    return d == t or d.endswith("." + t)


def classify(domain: str, owned_domains: dict[str, str] | None = None) -> str:
    """Return the source type for a cited domain.

    `owned_domains` maps a brand domain -> brand name. Any citation on a
    brand's own domain (or a subdomain of it) is labelled "owned", which takes
    priority over every other rule.
    """
    d = (domain or "").lower().lstrip(".")
    if not d:
        return "other"

    if owned_domains:
        for owned in owned_domains:
            if domain_matches(d, owned):
                return "owned"

    if d in INFRA_DOMAINS or any(domain_matches(d, i) for i in INFRA_DOMAINS):
        return "infrastructure"

    if d in DOMAIN_TYPES:
        return DOMAIN_TYPES[d]

    for known, label in DOMAIN_TYPES.items():
        if domain_matches(d, known):
            return label

    for suffix, label in SUFFIX_TYPES:
        if d.endswith(suffix):
            return label

    return "other"


def owned_brand(domain: str, owned_domains: dict[str, str]) -> str | None:
    """Which brand owns this cited domain, if any."""
    for owned, brand in owned_domains.items():
        if domain_matches(domain, owned):
            return brand
    return None
