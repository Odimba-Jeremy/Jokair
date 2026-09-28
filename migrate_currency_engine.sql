-- Moteur monétaire I-HUB : chaque montant garde sa devise d'origine.
-- `amount` reste l'équivalent USD pour compatibilité avec les comptes existants.
alter table if exists pharmacy
  add column if not exists price_currency text not null default 'FC'
  check (price_currency in ('USD', 'FC'));

alter table if exists patient_account_lines
  add column if not exists currency_origin text not null default 'USD'
  check (currency_origin in ('USD', 'FC')),
  add column if not exists amount_origin numeric,
  add column if not exists unit_price_origin numeric,
  add column if not exists amount_usd numeric,
  add column if not exists amount_fc numeric;

-- Existing pharmacy catalog is FC unless it is explicitly changed afterwards.
update pharmacy set price_currency = 'FC' where price_currency is null;

-- Existing account rows are historical USD values; do not try to infer a
-- currency from the number because that is precisely the ambiguity removed.
update patient_account_lines
set currency_origin = coalesce(currency_origin, 'USD'),
    amount_origin = coalesce(amount_origin, amount),
    amount_usd = coalesce(amount_usd, amount)
where amount is not null;
