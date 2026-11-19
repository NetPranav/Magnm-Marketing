import {
  forwardRef,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { cx } from './cx';

export interface FieldProps extends Omit<HTMLAttributes<HTMLLabelElement>, 'children'> {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  children: ReactNode;
}

/** Labeled form field wrapper with optional helper hint. */
export function Field({ label, htmlFor, hint, className, children, ...rest }: FieldProps) {
  return (
    <label className={cx('mg-field', className)} htmlFor={htmlFor} {...rest}>
      <span className="mg-label">{label}</span>
      {children}
      {hint ? <span className="mg-field-hint">{hint}</span> : null}
    </label>
  );
}

export type TextInputProps = InputHTMLAttributes<HTMLInputElement>;

/** Monochrome text input control. */
export function TextInput({ className, ...rest }: TextInputProps) {
  return <input className={cx('mg-input', className)} {...rest} />;
}

export type TextAreaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

/** Monochrome multi-line textarea control. */
export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { className, ...rest },
  ref,
) {
  return <textarea ref={ref} className={cx('mg-textarea', className)} {...rest} />;
});

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

/** Monochrome select dropdown control. */
export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select className={cx('mg-select', className)} {...rest}>
      {children}
    </select>
  );
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode;
}

/** Monochrome checkbox control with label. */
export function Checkbox({ label, className, ...rest }: CheckboxProps) {
  return (
    <label className={cx('mg-check', className)}>
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}
