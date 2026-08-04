"""Interactive glossary: plain-English definitions for every technical term.

One definition per concept, defined once and attached everywhere — metric
labels, table column headers, input controls and expanders all resolve against
this module rather than restating explanations inline.

Every entry follows the same three-part contract, because a beginner needs all
three to actually learn the concept:

1. **Definition** — what it is, in language that assumes no accounting training.
2. **Formula** — the exact calculation, when one exists.
3. **Example** — concrete numbers, so the formula stops being abstract.

Nothing in this module touches a computed value. It produces display strings for
Streamlit's ``help=`` parameter and for ``st.column_config`` tooltips; the
numerical integrity policy in :mod:`config` is unaffected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    """One glossary entry."""

    name: str
    definition: str
    formula: str = ""
    example: str = ""

    def tooltip(self) -> str:
        """Markdown for a Streamlit ``help=`` tooltip."""
        parts = [f"**{self.name}**", "", self.definition]
        if self.formula:
            parts += ["", f"**Formula:** {self.formula}"]
        if self.example:
            parts += ["", f"**Example:** {self.example}"]
        return "\n".join(parts)

    def markdown(self) -> str:
        """Fuller rendering for use inside an expander."""
        parts = [f"**{self.name}** — {self.definition}"]
        if self.formula:
            parts.append(f"- *Formula:* {self.formula}")
        if self.example:
            parts.append(f"- *Example:* {self.example}")
        return "\n".join(parts)


def _t(name, definition, formula="", example="") -> Term:
    return Term(name, definition, formula, example)


# --------------------------------------------------------------------------- #
# Earnings
# --------------------------------------------------------------------------- #

TERMS: dict[str, Term] = {}


def _add(*terms: Term) -> None:
    for term in terms:
        TERMS[term.name.lower()] = term


_add(
    _t(
        "EBITDA",
        "Earnings Before Interest, Taxes, Depreciation and Amortization. A rough proxy for "
        "the cash a business generates from operations, before how it is financed and before "
        "non-cash accounting charges. Buyers use it because it strips out decisions the new "
        "owner will make differently.",
        "Operating Income + Depreciation + Amortization",
        "Operating income of $2,380,000 plus $2,228,000 of depreciation and amortization gives "
        "EBITDA of $4,608,000.",
    ),
    _t(
        "Adjusted EBITDA",
        "EBITDA after removing one-off items and correcting accounting errors, so it reflects "
        "what the business actually earns on a normal, repeatable basis. This is the number a "
        "purchase price is usually a multiple of.",
        "EBITDA + accepted quality of earnings adjustments",
        "Reported EBITDA of $4,608,000 with net adjustments of –$720,000 gives Adjusted EBITDA "
        "of $3,888,000.",
    ),
    _t(
        "EBITDA Margin",
        "EBITDA as a share of revenue. Shows how many cents of operating cash the business "
        "keeps from every dollar of sales, and lets you compare companies of different sizes.",
        "EBITDA ÷ Revenue × 100",
        "EBITDA of $4,608,000 on revenue of $51,200,000 is a 9.00% margin.",
    ),
    _t(
        "Quality of Earnings",
        "The core deliverable of financial due diligence. A study of whether reported profit is "
        "real, repeatable and supported by evidence — or inflated by one-time gains, accounting "
        "choices and costs the seller has left out.",
        "",
        "A company reports $16m of EBITDA. After correcting an understated inventory reserve and "
        "normalising owner pay, the sustainable figure is $7.6m.",
    ),
    _t(
        "QoE Adjustment",
        "A single correction to reported earnings. Positive adjustments add back costs that will "
        "not recur; negative adjustments remove income that was not really earned or add costs "
        "the seller has not been paying.",
        "",
        "Adding back $840,000 of one-time transaction fees is a positive adjustment. Deducting "
        "$1,850,000 of revenue recognised too early is a negative one.",
    ),
    _t(
        "Quality of Earnings Delta",
        "The gap between reported EBITDA and Adjusted EBITDA. A large negative delta means "
        "reported profits overstate the truth.",
        "Adjusted EBITDA − Reported EBITDA",
        "Reported $4,608,000 versus adjusted $3,888,000 is a delta of –$720,000.",
    ),
    _t(
        "Net Income",
        "The bottom-line profit after every expense, including interest and taxes. Also called "
        "the 'bottom line' because it is the last row of the income statement.",
        "Revenue − all expenses − interest − taxes",
        "Pre-tax income of $1,050,000 less $320,000 of tax gives net income of $730,000.",
    ),
    _t(
        "Revenue",
        "The total value of goods or services sold in a period, before any costs. Recognised "
        "when the customer receives what they paid for — not necessarily when cash arrives.",
        "",
        "Selling 1,000 software subscriptions at $500 each produces $500,000 of revenue.",
    ),
)

# --------------------------------------------------------------------------- #
# Working capital
# --------------------------------------------------------------------------- #

_add(
    _t(
        "Net Working Capital",
        "The short-term money tied up in running the business day to day — what customers owe "
        "you and the stock on your shelves, less what you owe suppliers. Cash and debt are "
        "excluded because those are settled separately at closing.",
        "(Current Assets − Cash) − (Current Liabilities − Short-Term Debt)",
        "Operating current assets of $100 less operating current liabilities of $80 gives net "
        "working capital of $20.",
    ),
    _t(
        "NWC Peg",
        "The 'normal' level of working capital a buyer expects to receive with the business, "
        "usually a trailing average. If the seller delivers less than the peg at closing, the "
        "price is reduced; more, and the seller is paid the difference.",
        "Average net working capital over the trailing period",
        "Averaging $18m, $20m and $22m over three years sets a peg of $20m. Delivering $17m at "
        "close means a $3m price reduction.",
    ),
    _t(
        "Days Sales Outstanding",
        "How many days it takes, on average, to collect cash from customers after a sale. Rising "
        "DSO means collections are slowing — often the first sign of a revenue or credit problem.",
        "(Average Accounts Receivable ÷ Revenue) × 365",
        "Average receivables of $7,275,000 on revenue of $51,200,000 gives DSO of 51.86 days.",
    ),
    _t(
        "Days Inventory Outstanding",
        "How many days stock sits before it is sold. Rising DIO ties up cash and often signals "
        "obsolete or slow-moving inventory.",
        "(Average Inventory ÷ Cost of Sales) × 365",
        "Average inventory of $36,400,000 on cost of sales of $102,648,000 gives DIO of 129.43 days.",
    ),
    _t(
        "Days Payable Outstanding",
        "How many days the business takes to pay its own suppliers. Higher DPO conserves cash, "
        "but a sudden jump can mean the company is stretching payments because it is short.",
        "(Average Accounts Payable ÷ Cost of Sales) × 365",
        "Average payables of $16,050,000 on cost of sales of $102,648,000 gives DPO of 57.07 days.",
    ),
    _t(
        "Cash Conversion Cycle",
        "How many days cash is locked up in the operating cycle between paying suppliers and "
        "collecting from customers. Lower is better; negative means customers pay before "
        "suppliers do.",
        "DSO + DIO − DPO",
        "DSO of 67 days plus DIO of 129 less DPO of 57 gives a 139-day cycle.",
    ),
    _t(
        "Accounts Receivable",
        "Money customers owe for goods or services already delivered. An asset, but only worth "
        "something if it is actually collectable.",
        "",
        "Invoicing $8,650,000 that customers have not yet paid creates $8,650,000 of receivables.",
    ),
    _t(
        "Inventory",
        "Goods held for sale, plus raw materials and part-finished work. Must be carried at the "
        "lower of what it cost and what it can realistically be sold for.",
        "",
        "$38,600,000 of machined components sitting in a warehouse.",
    ),
    _t(
        "Accounts Payable",
        "Money the business owes suppliers for goods and services already received.",
        "",
        "$15,200,000 of unpaid supplier invoices.",
    ),
    _t(
        "Deferred Revenue",
        "Cash collected from a customer before the work has been done. It is a liability, not "
        "income — the business still owes the customer the service.",
        "",
        "Collecting $1,200 for a 12-month subscription creates $1,200 of deferred revenue that "
        "becomes revenue at $100 per month.",
    ),
)

# --------------------------------------------------------------------------- #
# Value bridge
# --------------------------------------------------------------------------- #

_add(
    _t(
        "Enterprise Value",
        "What the whole business is worth to all its funders combined, independent of how it is "
        "financed. Think of it as the price of the operating engine, before working out who "
        "owns what.",
        "Equity Value + Debt − Cash",
        "A business valued at $62m regardless of whether it carries debt or none.",
    ),
    _t(
        "Equity Value",
        "What the shareholders actually receive — enterprise value after settling debt, adding "
        "back cash, and adjusting for other obligations the buyer inherits.",
        "Enterprise Value + Cash − Debt − Debt-Like Items + Non-Operating Assets",
        "Enterprise value of $62m, plus $10m cash, less $14m debt, gives equity value of $58m.",
    ),
    _t(
        "Debt-Like Items",
        "Obligations that are not labelled 'debt' on the balance sheet but behave like it — cash "
        "the buyer must pay out after closing for something the seller already consumed. They "
        "reduce the price.",
        "",
        "Unpaid staff bonuses, an earnout owed on a past acquisition, or an unfunded pension "
        "obligation of $4,600,000.",
    ),
    _t(
        "Non-Operating Assets",
        "Assets the business owns but does not need in order to operate. The buyer can sell "
        "them, so they increase the price.",
        "",
        "A company aircraft used mainly for the owner's personal travel, appraised at $2,400,000.",
    ),
    _t(
        "Funded Debt",
        "Borrowed money the business must repay — bank loans, revolver drawings and the current "
        "portion of long-term debt.",
        "Short-Term Debt + Current Portion of Long-Term Debt + Long-Term Debt",
        "A $41m revolver plus $3m current maturities plus $19m of term loans is $63m of funded debt.",
    ),
    _t(
        "Net Debt",
        "Borrowings less the cash on hand — what the company would still owe if it used all its "
        "cash to pay down debt today.",
        "Funded Debt − Cash",
        "Debt of $63,000,000 less cash of $2,276,500 gives net debt of $60,723,500.",
    ),
    _t(
        "Cash-Free Debt-Free",
        "The standard M&A convention: the seller keeps the cash and settles the debt at closing, "
        "so the buyer acquires just the operating business.",
        "",
        "A target with $10m cash and $14m debt is bought for its enterprise value; those two "
        "balances are cleared at completion.",
    ),
    _t(
        "Transaction Value Bridge",
        "The step-by-step walk from what the business is worth (enterprise value) to what the "
        "shareholders are paid (equity value), showing every addition and deduction.",
        "EV + Cash − Debt − Debt-Like Items + Non-Operating Assets ± NWC true-up",
        "See the waterfall chart — each bar is one step of that walk.",
    ),
)

# --------------------------------------------------------------------------- #
# Valuation
# --------------------------------------------------------------------------- #

_add(
    _t(
        "Discounted Cash Flow",
        "A valuation method that projects the cash a business will generate over several years "
        "and converts it to today's value, because a dollar received in five years is worth "
        "less than a dollar today.",
        "Sum of (Future Cash Flow ÷ (1 + Discount Rate)^year) + Terminal Value",
        "$100 received in one year, discounted at 10%, is worth $90.91 today.",
    ),
    _t(
        "WACC",
        "Weighted Average Cost of Capital — the blended annual return that lenders and "
        "shareholders require. Used as the discount rate: the riskier the business, the higher "
        "the WACC and the lower the valuation.",
        "(Cost of Equity × Equity Weight) + (Cost of Debt × Debt Weight × (1 − Tax Rate))",
        "A WACC of 11.5% means future cash flows are discounted 11.5% for each year of waiting.",
    ),
    _t(
        "Unlevered Free Cash Flow",
        "The cash the business generates before any payments to lenders — available to everyone "
        "who funded it. 'Unlevered' means debt is ignored at this stage.",
        "NOPAT + Depreciation & Amortization − Capital Expenditure − Increase in NWC",
        "NOPAT of $6m plus $2m D&A less $2m capex less $1m working capital growth is $5m of UFCF.",
    ),
    _t(
        "NOPAT",
        "Net Operating Profit After Tax — operating profit with tax deducted but before interest, "
        "showing what the business earns regardless of how it is financed.",
        "EBIT × (1 − Tax Rate)",
        "EBIT of $8,000,000 at a 25% tax rate gives NOPAT of $6,000,000.",
    ),
    _t(
        "Terminal Value",
        "The estimated value of the business beyond the forecast period, since a company does "
        "not stop existing at the end of year five. Usually the majority of a DCF's value.",
        "Exit Multiple × Final-Year EBITDA, or Final-Year Cash Flow × (1 + g) ÷ (WACC − g)",
        "Year-5 EBITDA of $12m at a 9.0x exit multiple gives a terminal value of $108m.",
    ),
    _t(
        "Exit Multiple",
        "The EBITDA multiple assumed when the business is eventually sold, used to value the "
        "period beyond the forecast.",
        "Terminal Value ÷ Final-Year EBITDA",
        "A 9.0x multiple means a buyer pays nine times that year's EBITDA.",
    ),
    _t(
        "Gordon Growth",
        "An alternative terminal value method assuming cash flows grow forever at a small, "
        "steady rate. Only valid when the growth rate is below the discount rate.",
        "Final Cash Flow × (1 + g) ÷ (WACC − g)",
        "$10m growing at 2.5% forever, discounted at 11.5%, gives $113.9m.",
    ),
    _t(
        "Capital Expenditure",
        "Money spent on long-lived assets like equipment and buildings. It leaves the bank but "
        "does not appear as an expense — it is capitalised and depreciated over time.",
        "Change in Net PP&E + Depreciation",
        "Buying a $1,130,000 machine is capex, not an operating cost.",
    ),
    _t(
        "Mid-Year Convention",
        "A discounting refinement assuming cash arrives evenly through the year rather than all "
        "on the final day, which slightly increases present value.",
        "Discount at (year − 0.5) instead of the full year",
        "Year-1 cash discounted at 0.5 years rather than 1.0.",
    ),
    _t(
        "Implied EV / Adjusted EBITDA",
        "The valuation expressed as a multiple of normalised earnings — the standard way deal "
        "prices are quoted and compared.",
        "Enterprise Value ÷ Adjusted EBITDA",
        "An EV of $48.6m on Adjusted EBITDA of $3.888m is 12.5x.",
    ),
)

# --------------------------------------------------------------------------- #
# ASC 805 and purchase accounting
# --------------------------------------------------------------------------- #

_add(
    _t(
        "ASC 805",
        "The US accounting rule for business combinations. It requires a buyer to record every "
        "acquired asset and liability at fair value on day one, with anything left over becoming "
        "goodwill.",
        "",
        "Paying $48.6m for a business whose identifiable net assets are worth $20.2m creates "
        "$28.4m of goodwill.",
    ),
    _t(
        "Purchase Price Allocation",
        "The exercise of spreading the price paid across everything acquired — buildings, "
        "equipment, customer relationships, brand names — at fair value, with the remainder "
        "recorded as goodwill.",
        "Consideration − Fair Value of Identifiable Net Assets = Goodwill",
        "See the allocation bridge — each step assigns part of the price to something specific.",
    ),
    _t(
        "Goodwill",
        "The premium paid above the fair value of everything identifiable. It represents things "
        "you cannot put on a balance sheet individually — reputation, workforce, expected "
        "synergies. Goodwill is not amortized but is tested for impairment.",
        "Consideration Transferred − Fair Value of Identifiable Net Assets",
        "Paying $48,600,000 for net assets worth $20,235,000 records $28,365,000 of goodwill.",
    ),
    _t(
        "Bargain Purchase Gain",
        "The rare opposite of goodwill: paying less than the acquired net assets are worth. "
        "Accounting rules require you to double-check your work first, because it is far more "
        "often an error than a windfall.",
        "Fair Value of Identifiable Net Assets − Consideration (when positive)",
        "Paying $20,000,000 for net assets worth $20,235,000 produces a $235,000 gain.",
    ),
    _t(
        "Deferred Tax Liability",
        "A future tax bill created when an asset is worth more for accounting than for tax. The "
        "buyer will pay tax on that difference later, so it is recorded as a liability today.",
        "(Book Value − Tax Basis) × Marginal Tax Rate",
        "A $27,700,000 step-up at a 25% tax rate creates a $6,925,000 deferred tax liability.",
    ),
    _t(
        "Fair Value Step-Up",
        "The increase from an asset's old book value to what it is actually worth today. The "
        "buyer records the higher figure, which also raises future depreciation.",
        "Fair Value − Historical Book Value",
        "Equipment on the books at $2,450,000 but worth $3,650,000 carries a $1,200,000 step-up.",
    ),
    _t(
        "Identifiable Intangible Assets",
        "Non-physical assets that can be separated and valued individually — customer "
        "relationships, technology, brand names, non-compete agreements. They are recorded apart "
        "from goodwill and usually amortized.",
        "",
        "Customer relationships valued at $14,000,000 amortized over a 10-year life is "
        "$1,400,000 of annual amortization.",
    ),
    _t(
        "Opening Balance Sheet",
        "How the acquired business looks on the buyer's books on day one, after every asset and "
        "liability has been restated to fair value.",
        "",
        "The side-by-side table shows the seller's historical book values against the buyer's "
        "day-one fair values.",
    ),
    _t(
        "Stock Acquisition",
        "Buying the target's shares. The company's tax basis in its assets carries over "
        "unchanged, so any fair value step-up creates a deferred tax liability.",
        "",
        "Buying 100% of the shares — the entity continues, its tax history intact.",
    ),
    _t(
        "Asset Acquisition",
        "Buying the assets themselves (or making a 338(h)(10) election). Tax basis steps up "
        "alongside book value, so no deferred tax liability arises and the buyer gets larger "
        "future tax deductions.",
        "",
        "The same deal structured this way produces $0 of DTL instead of $6,925,000.",
    ),
    _t(
        "Marginal Tax Rate",
        "The tax rate applied to the next dollar of income. Used here to size the deferred tax "
        "consequences of fair value step-ups.",
        "",
        "A 25% rate applied to a $27,700,000 basis difference gives $6,925,000 of deferred tax.",
    ),
    _t(
        "Amortization",
        "Spreading the cost of an intangible asset across its useful life, in the same way "
        "depreciation spreads the cost of equipment.",
        "Fair Value ÷ Useful Life",
        "$14,000,000 of customer relationships over 10 years is $1,400,000 per year.",
    ),
    _t(
        "Depreciation",
        "Spreading the cost of a physical asset across the years it is used, rather than "
        "expensing it all at once.",
        "Typically Cost ÷ Useful Life",
        "A $780,000 machine over 10 years is $78,000 of annual depreciation.",
    ),
)

# --------------------------------------------------------------------------- #
# Accounting standards
# --------------------------------------------------------------------------- #

_add(
    _t(
        "ASC 606",
        "The revenue recognition standard. Revenue is recorded when the customer actually "
        "receives the benefit — not when the invoice is raised or the cash arrives.",
        "",
        "Billing $1,850,000 upfront for a three-year contract must be spread across 36 months, "
        "not recognised immediately.",
    ),
    _t(
        "ASC 330",
        "The inventory standard. Stock is carried at the lower of cost and what it can actually "
        "be sold for, so obsolete goods must be written down.",
        "",
        "$9,400,000 of stock unsold for 24 months requires a reserve against its carrying value.",
    ),
    _t(
        "ASC 326",
        "The credit loss standard (CECL). Companies must reserve for receivables they expect not "
        "to collect, based on realistic experience rather than optimism.",
        "",
        "Receivables aged over 120 days growing from $0.3m to $1.9m while the reserve stays flat "
        "understates losses.",
    ),
    _t(
        "ASC 350-40",
        "The internal-use software standard. Only development-stage costs may be capitalised; "
        "planning, maintenance and bug fixes must be expensed.",
        "",
        "Capitalising $2,300,000 of maintenance work overstates both assets and profit.",
    ),
    _t(
        "ASC 360",
        "The long-lived asset standard, covering when property and equipment must be tested for "
        "impairment and what may be capitalised versus expensed.",
        "",
        "Routine repairs must be expensed; only work extending an asset's life is capitalised.",
    ),
    _t(
        "ASC 450",
        "The contingencies standard, governing when a potential loss such as a lawsuit must be "
        "accrued and when it need only be disclosed.",
        "",
        "A probable and estimable legal loss must be recorded as a liability.",
    ),
    _t(
        "ASC 740",
        "The income tax standard, covering deferred taxes and uncertain tax positions.",
        "",
        "The deferred tax liability created by an acquisition step-up is recognised under this "
        "standard.",
    ),
    _t(
        "Three-Statement Model",
        "The income statement, balance sheet and cash flow statement viewed together. They must "
        "articulate — profit flows into equity, and cash movements reconcile to the cash balance.",
        "Assets = Liabilities + Equity",
        "If the balance sheet does not balance, something is wrong before any analysis begins.",
    ),
    _t(
        "Accrual Gap",
        "The difference between reported profit and the cash actually generated. A persistent "
        "gap suggests earnings are being recognised faster than cash is arriving.",
        "Net Income − Operating Cash Flow",
        "Net income of $10m against operating cash flow of $2m is an $8m gap worth investigating.",
    ),
    _t(
        "Pro Forma Adjustment",
        "An adjustment presenting results as if something had already happened for the full "
        "period. Supportable when it annualises an evidenced historical result; not when it "
        "annualises a plan.",
        "",
        "Annualising an acquired business's actual pre-purchase results is fair. Annualising "
        "hoped-for synergies is not.",
    ),
    _t(
        "Run-Rate",
        "Scaling a partial period up to a full year. Legitimate only where the underlying change "
        "has already occurred and is evidenced.",
        "Partial Period Result × (12 ÷ Months Elapsed)",
        "$1m earned in three months run-rates to $4m — but only if the level is genuinely "
        "sustainable.",
    ),
    _t(
        "Carve-Out / Standalone Costs",
        "Costs the business does not currently bear because a parent or owner provides the "
        "service free, but which the buyer will have to pay. A negative adjustment.",
        "",
        "A family office providing free treasury and IT means the buyer must add $1,100,000 of "
        "real annual cost.",
    ),
    _t(
        "Owner Compensation Normalization",
        "Restating an owner's pay to what the market would charge for the same role, since "
        "owners often pay themselves far above or below market.",
        "Market Salary − Actual Compensation",
        "A founder drawing $1.9m against a $0.95m market rate supports a $950,000 add-back.",
    ),
    _t(
        "Related-Party Transaction",
        "A deal with someone connected to the owners — often at a price that is not what an "
        "independent party would charge, so it must be restated to market.",
        "",
        "Renting three plants from the owner's own company at above-market rent.",
    ),
    _t(
        "Material Weakness",
        "A flaw in a company's financial controls serious enough that a significant error could "
        "go undetected. It undermines confidence in every number reported.",
        "",
        "One person raising invoices, approving credit notes and reconciling the ledger.",
    ),
    _t(
        "Going Concern",
        "Substantial doubt about whether a business can survive the next twelve months. A "
        "threshold issue that comes before any discussion of price.",
        "",
        "A company that will exhaust its borrowing capacity in two quarters.",
    ),
)

# --------------------------------------------------------------------------- #
# SEC filings and the narrative scanner
# --------------------------------------------------------------------------- #

_add(
    _t(
        "10-K",
        "A public company's annual report to the SEC — audited financials plus a detailed "
        "narrative on risks, performance and legal matters.",
        "",
        "Filed once a year, typically 200 to 600 pages.",
    ),
    _t(
        "10-Q",
        "The quarterly equivalent of a 10-K. Shorter, unaudited, and usually cross-references "
        "the annual filing rather than restating it.",
        "",
        "Filed after each of the first three fiscal quarters.",
    ),
    _t(
        "Item 1A — Risk Factors",
        "The section where a company lists what could go wrong. Written by lawyers to be "
        "defensible, so the signal is in what is *new* this year rather than the boilerplate.",
        "",
        "A risk factor about receivable collectability appearing for the first time is worth "
        "investigating.",
    ),
    _t(
        "Item 7 — MD&A",
        "Management's Discussion and Analysis — management explaining in their own words why "
        "results moved the way they did.",
        "",
        "Where a company explains a margin decline it would rather you did not notice.",
    ),
    _t(
        "SEC EDGAR",
        "The SEC's public database of company filings. Free, complete, and the primary source "
        "for any analysis of a US-listed company.",
        "",
        "Every 10-K and 10-Q filed since the mid-1990s is available there.",
    ),
    _t(
        "Narrative Delta",
        "The measured change in a filing's language versus the prior year. What a company newly "
        "adds usually means something happened.",
        "",
        "Risk factors growing from 10,933 to 10,794 words with 32 passages added and 43 removed.",
    ),
    _t(
        "Impact Score",
        "A ranking combining how serious a risk is, how often it is mentioned, and whether the "
        "language is new this year. New disclosure is weighted double.",
        "Severity Weight × (1 + ln(1 + Mentions)) × YoY Emphasis",
        "A Critical risk (weight 4) mentioned 8 times and new this year scores 4 × 3.20 × 2.0 = 25.6.",
    ),
    _t(
        "Severity",
        "How serious a flagged risk is for the deal, on a four-point scale from Low to Critical. "
        "Critical items are threshold issues that come before pricing.",
        "",
        "A going-concern warning is Critical; boilerplate litigation language is Low.",
    ),
    _t(
        "EBITDA Haircut",
        "A dollar reduction to Adjusted EBITDA that you assign to a risk you judge to be real "
        "and quantifiable. Entered as a positive number and applied as a deduction.",
        "",
        "Assigning $480,000 to an under-reserved receivable reduces Adjusted EBITDA by that amount.",
    ),
    _t(
        "Flesch-Kincaid Grade",
        "A readability score approximating the school grade needed to understand the text. "
        "Filings that get harder to read without the business getting more complex are a "
        "documented warning sign.",
        "0.39 × (Words ÷ Sentences) + 11.8 × (Syllables ÷ Words) − 15.59",
        "A grade of 19.1 means the text is harder than a typical university text.",
    ),
    _t(
        "Hedging Density",
        "How often uncertainty words — may, could, potentially, no assurance — appear per "
        "thousand words. Rising density suggests management is becoming less confident.",
        "(Hedging Terms ÷ Total Words) × 1,000",
        "309 hedging terms in 10,794 words is 28.63 per thousand.",
    ),
)

# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #

# Labels used in the UI that do not exactly match a glossary key.
ALIASES: dict[str, str] = {
    "adjusted ebitda margin %": "ebitda margin",
    "ebitda margin %": "ebitda margin",
    "ebitda (as reported)": "ebitda",
    "reported ebitda": "ebitda",
    "net income (gaap)": "net income",
    "nwc as % of revenue": "net working capital",
    "operating net working capital": "net working capital",
    "net working capital balance": "net working capital",
    "proposed nwc peg": "nwc peg",
    "working capital peg": "nwc peg",
    "days sales outstanding (dso)": "days sales outstanding",
    "days inventory outstanding (dio)": "days inventory outstanding",
    "days payable outstanding (dpo)": "days payable outstanding",
    "dso": "days sales outstanding",
    "dio": "days inventory outstanding",
    "dpo": "days payable outstanding",
    "total debt-like items": "debt-like items",
    "total non-operating assets": "non-operating assets",
    "net funded debt": "net debt",
    "implied equity value": "equity value",
    "consideration transferred": "purchase price allocation",
    "identifiable net assets": "purchase price allocation",
    "deferred tax liability on step-ups": "deferred tax liability",
    "annual amortization": "amortization",
    "unlevered free cash flow": "unlevered free cash flow",
    "present value of ufcf": "unlevered free cash flow",
    "pv of forecast cash flows": "discounted cash flow",
    "pv of terminal value": "terminal value",
    "wacc (%)": "wacc",
    "exit ebitda multiple": "exit multiple",
    "terminal growth (%)": "gordon growth",
    "marginal tax rate (%)": "marginal tax rate",
    "cash tax rate (%)": "marginal tax rate",
    "day 1 fair value": "fair value step-up",
    "fair value step-up / (down)": "fair value step-up",
    "historical book value": "opening balance sheet",
    "transaction structure": "stock acquisition",
    "impact score": "impact score",
    "mentions": "narrative delta",
    "flags raised": "severity",
    "goodwill": "goodwill",
    "total qoe adjustments": "qoe adjustment",
    "net qoe adjustments": "qoe adjustment",
    "capital expenditures": "capital expenditure",
    "capital expenditure (% of revenue)": "capital expenditure",
    "revenue cagr (%)": "discounted cash flow",
    "target ebitda margin (%)": "ebitda margin",
    "net working capital (% of revenue)": "net working capital",
    "depreciation & amortization (% of revenue)": "amortization",
}

_PUNCT = re.compile(r"[^a-z0-9%&/\- ]+")


def _normalise(label: str) -> str:
    return _PUNCT.sub("", (label or "").strip().lower()).strip()


def lookup(label: str) -> Term | None:
    """Resolve a UI label to a glossary term, or None."""
    key = _normalise(label)
    if not key:
        return None
    if key in TERMS:
        return TERMS[key]
    if key in ALIASES:
        return TERMS.get(ALIASES[key])
    # Fall back to the longest term name contained in the label, so
    # "Adjusted EBITDA (as reported)" still resolves.
    matches = [name for name in TERMS if name in key]
    if matches:
        return TERMS[max(matches, key=len)]
    return None


def help_for(label: str, fallback: str = "") -> str | None:
    """Tooltip markdown for a UI label, or the fallback."""
    term = lookup(label)
    if term is not None:
        return term.tooltip()
    return fallback or None


def combine(label: str, extra: str) -> str:
    """Glossary tooltip plus a context-specific note."""
    term = lookup(label)
    if term is None:
        return extra
    return f"{term.tooltip()}\n\n---\n\n{extra}"


def term_key(*labels: str) -> str:
    """Markdown definition list for the labels given, for use in an expander."""
    seen: set[str] = set()
    lines: list[str] = []
    for label in labels:
        term = lookup(label)
        if term is None or term.name in seen:
            continue
        seen.add(term.name)
        lines.append(term.markdown())
    return "\n\n".join(lines)


def all_terms() -> list[Term]:
    """Every term, alphabetically — for the glossary reference page."""
    return sorted(TERMS.values(), key=lambda term: term.name.lower())


# --------------------------------------------------------------------------- #
# "How is this calculated?" explainers
# --------------------------------------------------------------------------- #

EXPLAINERS: dict[str, str] = {
    "qoe_bridge": """
The bridge walks from the **bottom line of the income statement** up to the earnings figure a
buyer actually prices.

**Step 1 — Undo the financing and accounting decisions.** Start at Net Income and add back the
items a new owner will handle differently:

- *Income tax* — depends on the seller's structure, not the business
- *Interest* — depends on how much debt the seller chose to carry
- *Depreciation & amortization* — non-cash charges reflecting past purchases

That gives you **EBITDA**, a rough proxy for operating cash generation.

**Step 2 — Normalise it.** EBITDA is still distorted by one-offs and errors. Each adjustment
either adds back a cost that will not recur, or removes income that was not really earned:

| Direction | Meaning | Example |
|---|---|---|
| **Positive** | Cost the buyer will not inherit | One-time legal settlement |
| **Negative** | Income overstated, or a cost the seller has not been paying | Revenue booked too early |

The result is **Adjusted EBITDA** — the number the purchase price is a multiple of.

> **Why negatives matter.** Beginners book almost only add-backs. Real engagements produce
> negatives in similar volume: under-reserved receivables, revenue recognised early, costs a
> parent currently absorbs. A schedule that only goes one direction is a warning sign about the
> analyst, not good news for the buyer.
""",
    "nwc": """
Net working capital is the **cash tied up in day-to-day operations**.

**The calculation excludes cash and debt deliberately.** Deals are struck *cash-free and
debt-free*: the seller keeps the cash and clears the borrowings at closing, so neither belongs in
the operating figure.

```
  Accounts receivable        what customers owe you
+ Inventory                  stock on the shelves
+ Prepaid expenses           costs paid in advance
─────────────────────────
= Operating current assets

  Accounts payable           what you owe suppliers
+ Accrued liabilities        costs incurred, not yet billed
+ Deferred revenue           cash taken for work not yet done
─────────────────────────
= Operating current liabilities

Net working capital = Operating current assets − Operating current liabilities
```

**Why the peg exists.** Working capital swings month to month. A buyer prices the business
assuming a *normal* level comes with it, so the parties agree a peg — usually a trailing average.
Deliver less at closing and the price drops; deliver more and the seller is paid the difference.

> Setting the peg on the **year-end balance alone** flatters a seller who has chased collections
> and delayed supplier payments into the closing date. A trailing average is harder to manipulate.
""",
    "efficiency": """
These three ratios convert balance sheet figures into **days**, which makes them comparable
across companies and across time.

| Metric | Question it answers | Formula |
|---|---|---|
| **DSO** | How long to collect from customers? | (Avg. receivables ÷ revenue) × 365 |
| **DIO** | How long does stock sit? | (Avg. inventory ÷ cost of sales) × 365 |
| **DPO** | How long do we take to pay suppliers? | (Avg. payables ÷ cost of sales) × 365 |

**Averages, not closing balances.** Each uses the average of the opening and closing balance,
because a single year-end snapshot can be managed. This is the convention in TAS working papers.

**The cash conversion cycle** ties them together: `DSO + DIO − DPO`. It is the number of days
cash is locked up between paying a supplier and collecting from a customer.

> **What to look for.** Direction matters more than level. Receivables growing faster than
> revenue means either collections are deteriorating or revenue is being recognised ahead of the
> cash. Both are quantifiable findings.
""",
    "value_bridge": """
The value bridge answers a question the DCF does not: **the business is worth X — so what do the
shareholders actually get?**

```
  Enterprise Value              what the operating business is worth
+ Cash                          seller's cash comes off the table
− Funded debt                   borrowings repaid at closing
− Debt-like items               obligations the buyer inherits
+ Non-operating assets          assets not needed to run the business
± Working capital true-up       delivered NWC versus the agreed peg
──────────────────────────
= Implied Equity Value          what shareholders receive
```

**Debt-like items are where diligence earns its fee.** They are obligations that do not carry the
word "debt" but behave exactly like it — accrued bonuses, deferred acquisition payments, unfunded
pensions, environmental clean-up, deferred maintenance. Each is cash the buyer pays *after*
closing for something the seller already consumed, so each reduces the price.

**Non-operating assets run the other way.** Surplus land, an owner's aircraft, a key-person
insurance policy — the buyer can sell them, so they increase the price.
""",
    "dcf": """
A discounted cash flow values the business by **projecting the cash it will generate and
converting that to today's money**.

**Step 1 — Project.** Revenue grows at the CAGR you set; the EBITDA margin moves toward your
target over the horizon.

**Step 2 — Convert profit to cash** for each year:

```
  EBITDA
− Depreciation & amortization
─────────────────────────
= EBIT
× (1 − tax rate)
─────────────────────────
= NOPAT
+ Depreciation & amortization      added back — non-cash
− Capital expenditure              real cash out
− Increase in working capital      growth consumes cash
─────────────────────────
= Unlevered Free Cash Flow
```

**Step 3 — Discount.** A dollar in five years is worth less than a dollar today, so each year is
divided by `(1 + WACC)^year`.

**Step 4 — Add terminal value**, since the business does not stop at year five. Either an exit
multiple on final-year EBITDA, or Gordon Growth assuming modest perpetual growth.

> **This DCF is anchored on *your* Adjusted EBITDA.** Change one adjustment in the working papers
> and the enterprise value moves with it. That link is the point of the exercise.
""",
    "ppa": """
When one company buys another, ASC 805 requires the buyer to **restate everything acquired to
fair value on day one**, with anything left over becoming goodwill.

```
  Consideration transferred            the price paid
− Book value of net assets acquired    what the seller had recorded
− Fair value step-up on tangibles      assets worth more than book
− Identifiable intangibles             customer lists, technology, brand
+ Deferred tax liability               future tax on the step-ups
──────────────────────────────────
= Goodwill                             the residual
```

**Goodwill is a plug, not a valuation.** It is whatever remains after everything identifiable has
been recorded — reputation, workforce, expected synergies.

**Why the structure changes the answer.** Deferred tax arises only where book and tax values
diverge:

- **Stock acquisition** — the target's tax basis carries over unchanged, so every step-up creates
  a deferred tax liability. That DTL reduces identifiable net assets, so it **increases goodwill
  dollar for dollar**.
- **Asset acquisition** (or a 338(h)(10) election) — tax basis steps up too, so **no DTL arises**
  and goodwill is lower by exactly that amount.

> **No deferred tax is recorded on goodwill itself.** That exemption is why goodwill is the
> residual rather than an input to the tax calculation.

**A bargain purchase gain** — paying less than the net assets are worth — is rare and is far more
often an allocation error than a windfall. The standard requires you to re-check your work before
recognising one.
""",
    "opening_bs": """
This is **how the acquired business appears on the buyer's books on day one**, side by side with
how the seller had it recorded.

| Column | Meaning |
|---|---|
| **Historical Book Value** | What the seller carried on their balance sheet |
| **Day 1 Fair Value** | What the buyer records under ASC 805 |
| **Fair Value Adjustment** | The difference — the step-up or step-down |

**Three things change and are worth understanding:**

1. **Cash and funded debt drop to zero.** Under the cash-free debt-free convention the seller
   keeps the cash and settles the borrowings at closing, so neither transfers.
2. **The seller's goodwill is eliminated.** It related to *their* past acquisitions. It is
   replaced by the goodwill this allocation computes.
3. **Intangibles appear from nowhere.** Customer relationships and brand names were built
   internally, so the seller never recorded them — but the buyer paid for them, so they must be
   recognised.
""",
    "risk_scan": """
The scanner reads the narrative sections of a filing — **Item 1A (Risk Factors)** and **Item 7
(MD&A)** — and flags the language that matters in diligence.

**How a flag is scored:**

```
Impact Score = Severity Weight × (1 + ln(1 + Mentions)) × Year-over-Year Emphasis
```

- **Severity weight** runs Low 1.0 → Critical 4.0
- **Mentions** uses a log so a verbose filer does not dominate the ranking
- **Year-over-year emphasis** is **2.0 for language new this year**, 1.5 for materially expanded

> **New language is the whole point.** Boilerplate a company has repeated for a decade tells you
> nothing. A risk factor that did not exist last year usually means something happened.

**What this cannot do.** It counts pattern matches. It cannot read context, and it cannot tell a
hypothetical risk from a realised one. Treat a high score as an instruction to go and read the
section — every flag carries the sentence that triggered it precisely so you can dismiss it
quickly when the context does not support it.
""",
    "narrative_delta": """
This compares the current filing's language against the prior year's, **sentence by sentence**.

- **Red** — language *added* this year
- **Green** — language *removed* since last year
- **Similarity %** — how much of the section is unchanged

**Why sentences rather than words.** A word-level comparison across two 70,000-character risk
sections is both slow and unreadable. Sentence moves are what a reviewer actually wants to see.

> **Read the red first.** What a company chose to add is the finding. Companies do not lengthen
> their risk disclosure for fun — they do it because counsel advised them to after something
> changed.
""",
    "comparison": """
This scores your working papers against the conclusions the engagement team actually reached.

**How your adjustments are matched.** Labels are paired by word overlap, so a paraphrase still
matches. Every entry lands in one of three buckets:

| Outcome | Meaning |
|---|---|
| **Identified** | You found it — compare your figure against theirs |
| **Missed** | In the issued report, absent from your papers |
| **Not in the issued report** | You booked something they did not |

> **That last bucket is not automatically wrong.** The answer key is one team's judgement. If you
> found something real and can support it, credit yourself — then ask why they did not.

**Coverage %** is the share of *accepted* adjustments you identified. **Variance** is the dollar
gap between your Adjusted EBITDA and theirs — the number that would move a purchase price.
""",
}


def explainer(key: str) -> str:
    """Explainer markdown for a component, or an empty string."""
    return EXPLAINERS.get(key, "").strip()
