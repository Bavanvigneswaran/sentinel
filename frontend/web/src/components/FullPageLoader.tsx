export function FullPageLoader() {
  return (
    <div className="flex min-h-svh items-center justify-center">
      <div
        className="size-6 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent"
        role="status"
        aria-label="Loading"
        data-testid="page-loading"
      />
    </div>
  )
}
