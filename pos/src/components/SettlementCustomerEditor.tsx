import { useEffect, useState } from 'react';
import { Phone, Search, UserPlus, X } from 'lucide-react';
import { Button, Input, Spinner } from '@ury/ui';
import { searchCustomers } from '../lib/customer-api';
import type {
  SettlementCustomerInput,
  SettlementCustomerSummary,
} from '../lib/settlement-api';
import { t } from '../i18n';

interface SettlementCustomerEditorProps {
  value: SettlementCustomerInput;
  currentCustomer: SettlementCustomerSummary;
  onChange: (customer: SettlementCustomerInput) => void;
  disabled?: boolean;
  requiredRealCustomer?: boolean;
  defaultCustomer?: string | null;
}

interface CustomerSearchResult {
  name: string;
  customer_name?: string;
  mobile_number?: string;
  content?: string;
}

function resultName(customer: CustomerSearchResult): string {
  return customer.customer_name
    || customer.content?.match(/Customer Name : ([^|]+)/)?.[1]?.trim()
    || customer.name;
}

function resultPhone(customer: CustomerSearchResult): string {
  return customer.mobile_number
    || customer.content?.match(/Mobile Number : ([^|]+)/)?.[1]?.trim()
    || '';
}

export function SettlementCustomerEditor({
  value,
  currentCustomer,
  onChange,
  disabled,
  requiredRealCustomer,
  defaultCustomer,
}: SettlementCustomerEditorProps) {
  const [editingExisting, setEditingExisting] = useState(false);
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<CustomerSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selectedSummary, setSelectedSummary] = useState<SettlementCustomerSummary>(currentCustomer);

  useEffect(() => {
    setSelectedSummary(currentCustomer);
  }, [currentCustomer]);

  useEffect(() => {
    if (!editingExisting || !search.trim()) {
      setResults([]);
      setSearchError(null);
      setIsSearching(false);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setIsSearching(true);
      searchCustomers(search, 8)
        .then((customers) => {
          if (!cancelled) setResults(customers as CustomerSearchResult[]);
        })
        .catch(() => {
          if (!cancelled) setSearchError(t('customer.failed_search'));
        })
        .finally(() => {
          if (!cancelled) setIsSearching(false);
        });
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [editingExisting, search]);

  const selectedExisting = 'existing' in value ? value.existing : null;
  const selectedExistingIsDefault = !!selectedExisting && selectedExisting === defaultCustomer;

  if ('new' in value) {
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 font-medium text-blue-900">
            <UserPlus className="h-4 w-4" />
            {t('settlement.customer.new_title')}
          </div>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setSelectedSummary(currentCustomer);
              onChange({ existing: currentCustomer.id });
              setEditingExisting(true);
            }}
            disabled={disabled}
          >
            {t('settlement.customer.choose_existing')}
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm font-medium text-gray-700">
            <span>{t('customer.name_label')} *</span>
            <Input
              value={value.new.customer_name}
              onChange={(event) => onChange({
                new: { ...value.new, customer_name: event.target.value },
              })}
              disabled={disabled}
              autoComplete="name"
            />
          </label>
          <label className="space-y-1 text-sm font-medium text-gray-700">
            <span>{t('customer.phone_label')} *</span>
            <Input
              type="tel"
              value={value.new.mobile_no}
              onChange={(event) => onChange({
                new: { ...value.new, mobile_no: event.target.value },
              })}
              disabled={disabled}
              autoComplete="tel"
            />
          </label>
        </div>
        <label className="block space-y-1 text-sm font-medium text-gray-700">
          <span>{t('settlement.customer.tax_id')}</span>
          <Input
            value={value.new.tax_id ?? ''}
            onChange={(event) => onChange({
              new: { ...value.new, tax_id: event.target.value },
            })}
            disabled={disabled}
          />
        </label>
        <p className="text-xs text-blue-800">{t('settlement.customer.created_on_confirm')}</p>
      </div>
    );
  }

  if (!editingExisting) {
    const customerName = selectedExisting === selectedSummary.id
      ? selectedSummary.name
      : selectedExisting;
    const customerPhone = selectedExisting === selectedSummary.id ? selectedSummary.phone : '';
    return (
      <div className={`rounded-lg border p-4 ${
        requiredRealCustomer && selectedExistingIsDefault
          ? 'border-amber-300 bg-amber-50'
          : 'border-gray-200 bg-gray-50'
      }`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-medium text-gray-900">{customerName}</p>
            {customerPhone && (
              <p className="mt-1 flex items-center gap-1 text-xs text-gray-600">
                <Phone className="h-3 w-3" /> {customerPhone}
              </p>
            )}
            {requiredRealCustomer && selectedExistingIsDefault && (
              <p className="mt-2 text-xs font-medium text-amber-800">
                {t('settlement.customer.real_customer_required')}
              </p>
            )}
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setEditingExisting(true)}
            disabled={disabled}
          >
            {t('common.change')}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setSearchError(null);
            }}
            className="ps-9"
            placeholder={t('customer.search_placeholder')}
            disabled={disabled}
            autoFocus
          />
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => {
            setEditingExisting(false);
            setSearch('');
          }}
          aria-label={t('common.cancel')}
          disabled={disabled}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {isSearching && <Spinner hideMessage message={t('common.searching')} className="h-5 w-5" />}
      {searchError && <p className="text-sm text-red-600">{searchError}</p>}

      {!isSearching && search.trim() && results.length === 0 && !searchError && (
        <p className="text-sm text-gray-500">{t('customer.no_customers_found')}</p>
      )}

      {results.length > 0 && (
        <div className="max-h-44 divide-y overflow-y-auto rounded-md border border-gray-200">
          {results.map((customer) => (
            <button
              key={customer.name}
              type="button"
              className="block w-full px-3 py-2 text-start hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
              onClick={() => {
                setSelectedSummary({
                  id: customer.name,
                  name: resultName(customer),
                  phone: resultPhone(customer),
                });
                onChange({ existing: customer.name });
                setEditingExisting(false);
                setSearch('');
              }}
              disabled={disabled}
            >
              <span className="block text-sm font-medium text-gray-900">{resultName(customer)}</span>
              <span className="block text-xs text-gray-500">{resultPhone(customer)}</span>
            </button>
          ))}
        </div>
      )}

      <Button
        type="button"
        variant="outline"
        className="w-full gap-2"
        onClick={() => {
          onChange({
            new: {
              customer_name: search.trim(),
              mobile_no: '',
              tax_id: '',
            },
          });
          setEditingExisting(false);
        }}
        disabled={disabled}
      >
        <UserPlus className="h-4 w-4" />
        {t('settlement.customer.create_new')}
      </Button>
    </div>
  );
}
