-- sql/schema.sql

create table if not exists existing_products (
  id text primary key,
  retailer text not null,
  product_name text not null,
  size_raw text,
  upc text,
  image_url text,
  product_url text
);

create table if not exists scraped_products (
  id text primary key,
  retailer text not null,
  product_name text not null,
  size_raw text,
  upc text,
  product_url text,
  scraped_at timestamptz default now()
);

create table if not exists product_matches (
  id bigserial primary key,
  scraped_product_id text not null references scraped_products(id) on delete cascade,
  matched_existing_id text references existing_products(id) on delete set null,
  match_score double precision not null,
  match_method text not null,
  matched_at timestamptz default now(),
  unique(scraped_product_id)
);

create index if not exists idx_existing_upc on existing_products(upc);
create index if not exists idx_scraped_upc on scraped_products(upc);
create index if not exists idx_matches_existing on product_matches(matched_existing_id);