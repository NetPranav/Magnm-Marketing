import type {
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
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
export function TextArea({ className, ...rest }: TextAreaProps) {
  return <textarea className={cx('mg-textarea', className)} {...rest} />;
}

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

/** Monochrome select dropdown control. */
export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select className={cx('mg-select', className)} {...rest}>
      {children}
    </select>
  );
}
