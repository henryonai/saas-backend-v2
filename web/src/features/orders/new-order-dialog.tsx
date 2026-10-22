import { PlusIcon } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { UpgradeNotice } from '@/shared/components/upgrade-notice';
import { Alert, AlertDescription } from '@/shared/components/ui/alert';
import { Button } from '@/shared/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/components/ui/dialog';
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Spinner } from '@/shared/components/ui/spinner';
import { errorMessage, isPlanLimit } from '@/shared/lib/errors';
import { dollarsToCents, formatMoney } from '@/shared/lib/format';
import { useCreateOrder } from './api';
import { MAX_ITEM_LENGTH, MAX_QUANTITY } from './status';

type Errors = Partial<Record<'item' | 'quantity' | 'price', string>>;

export function NewOrderDialog() {
  const { t } = useTranslation();
  const create = useCreateOrder();
  const [open, setOpen] = useState(false);
  const [item, setItem] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [price, setPrice] = useState('');
  const [errors, setErrors] = useState<Errors>({});

  const quantityValue = Number(quantity);
  const priceCents = dollarsToCents(price);
  const total = Number.isInteger(quantityValue) && quantityValue > 0 && priceCents !== null ? priceCents * quantityValue : null;

  function reset() {
    setItem('');
    setQuantity('1');
    setPrice('');
    setErrors({});
    create.reset();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found: Errors = {};
    if (!item.trim()) found.item = t('orders.new.itemRequired');
    if (!Number.isInteger(quantityValue) || quantityValue < 1 || quantityValue > MAX_QUANTITY) {
      found.quantity = t('orders.new.quantityInvalid', { max: MAX_QUANTITY });
    }
    if (priceCents === null) found.price = t('orders.new.priceInvalid');
    setErrors(found);
    if (Object.keys(found).length > 0 || priceCents === null) return;

    try {
      const order = await create.mutateAsync({ item: item.trim(), quantity: quantityValue, price_cents: priceCents });
      toast.success(t('orders.new.created', { item: order.item }));
      setOpen(false);
      reset();
    } catch {
      // shown in the dialog
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <PlusIcon />
          {t('orders.new.open')}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} noValidate className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t('orders.new.title')}</DialogTitle>
            <DialogDescription>{t('orders.new.description')}</DialogDescription>
          </DialogHeader>
          {create.isError &&
            (isPlanLimit(create.error) ? (
              <UpgradeNotice message={errorMessage(create.error, t)} />
            ) : (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(create.error, t)}</AlertDescription>
              </Alert>
            ))}
          <FieldGroup>
            <Field data-invalid={!!errors.item}>
              <FieldLabel htmlFor="order-item">{t('orders.new.item')}</FieldLabel>
              <Input
                id="order-item"
                maxLength={MAX_ITEM_LENGTH}
                value={item}
                aria-invalid={!!errors.item}
                onChange={(event) => setItem(event.target.value)}
              />
              {errors.item && <FieldError>{errors.item}</FieldError>}
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field data-invalid={!!errors.quantity}>
                <FieldLabel htmlFor="order-quantity">{t('orders.new.quantity')}</FieldLabel>
                <Input
                  id="order-quantity"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={MAX_QUANTITY}
                  step={1}
                  value={quantity}
                  aria-invalid={!!errors.quantity}
                  onChange={(event) => setQuantity(event.target.value)}
                />
                {errors.quantity && <FieldError>{errors.quantity}</FieldError>}
              </Field>
              <Field data-invalid={!!errors.price}>
                <FieldLabel htmlFor="order-price">{t('orders.new.unitPrice')}</FieldLabel>
                <Input
                  id="order-price"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={price}
                  aria-invalid={!!errors.price}
                  onChange={(event) => setPrice(event.target.value)}
                />
                {errors.price ? <FieldError>{errors.price}</FieldError> : <FieldDescription>USD</FieldDescription>}
              </Field>
            </div>
          </FieldGroup>
          <p className="text-sm text-muted-foreground">
            {t('orders.new.total')}: <span className="font-medium text-foreground tabular-nums">{total === null ? '—' : formatMoney(total)}</span>
          </p>
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Spinner />}
              {t('orders.new.submit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
